import stat
import paramiko
from datetime import datetime
from ftplib import error_perm
from time import sleep, time
import sys
import os
import socket
import json
import locale
import threading
import warnings

warnings.filterwarnings(action='ignore', module='.*paramiko.*')

if __name__ != '__main__':
    try:
        from uhf_host_common.interface.logger_client import orisolLog
        logger_import_message = 'reference orisol log interface from uhf_host_common'
    except ImportError:
        from uhf_host_file_manager.interface.logger_client import orisolLog
        logger_import_message = 'reference orisol log interface from uhf_host_file_manager'
    print(logger_import_message)


def synchronized(func):
    func.__lock__ = threading.RLock()

    def lock_func(*args, **kwargs):
        with func.__lock__:
            # print('----lock',func.__name__)
            return func(*args, **kwargs)

    return lock_func


UNITS_MAPPING = [
    (1 << 50, ' PB'),
    (1 << 40, ' TB'),
    (1 << 30, ' GB'),
    (1 << 20, ' MB'),
    (1 << 10, ' KB'),
    (1, (' byte', ' bytes')),
]

Module_Name = 'SFtpClient'


def pretty_size(bytes, units=UNITS_MAPPING):
    """Get human-readable file sizes.
    simplified version of https://pypi.python.org/pypi/hurry.filesize/
    """
    for factor, suffix in units:
        if bytes >= factor:
            break
    amount = int(bytes / factor)

    if isinstance(suffix, tuple):
        singular, multiple = suffix
        if amount == 1:
            suffix = singular
        else:
            suffix = multiple
    return str(amount) + suffix


class BulkTag():
    retry_count = 3
    src_mount = ''
    dst_mount = ''
    src_filename_list = []
    dst_filename_list = []
    bulk_index = 0

    def init_parameter(self,
                       dst_mount=None,
                       dst_filename_list=None,
                       src_mount=None,
                       src_filename_list=None):
        self.src_filename_list.clear()
        self.dst_filename_list.clear()
        self.bulk_index = 0

        if src_mount is not None:
            self.src_mount = src_mount
        if dst_mount is not None:
            self.dst_mount = dst_mount

        if src_filename_list is not None:
            self.src_filename_list = src_filename_list.copy()
        if dst_filename_list is not None:
            self.dst_filename_list = dst_filename_list.copy()
        self.retry_count = 3

    def get_retry_count(self):
        return self.retry_count

    def set_retry_count(self, count):
        self.retry_count = count

    def get_index(self):
        return self.bulk_index

    def set_index(self, index):
        self.bulk_index = index

    def get_src_filename_by_index(self):
        return self.src_filename_list[self.bulk_index]

    def get_dst_filename_by_index(self):
        return self.dst_filename_list[self.bulk_index]

    def get_src_mount(self):
        return self.src_mount

    def get_dst_mount(self):
        return self.dst_mount

    def get_mounts(self):
        return self.src_mount, self.dst_mount

    def get_length_src_filelist(self):
        return len(self.src_filename_list)

    def get_length_dst_filelist(self):
        return len(self.dst_filename_list)


class SSH_Ftp_Client():
    host = ''
    username = ''
    password = ''
    port = 22
    conn_timeout = 5
    remote_pwd_shadow = '/'
    working_directory = {'remote': '/', 'remote_backup': '/'}
    __is_login = False
    local_current_directory = os.getcwd()
    local_tmp_directory = os.getcwd()
    continuous_file_numbers = 128
    access_status = -1
    access_timestamp = time()
    access_diff = 10
    bulktag = BulkTag()

    def __new__(cls, *args, **kw):
        if not hasattr(cls, '_instance'):
            cls._instance = super(SSH_Ftp_Client, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        super(SSH_Ftp_Client, self).__init__()

    def init_parameter(
        self,
        host,
        username='anonymous',
        password='anonymous',
        local_current_directory=os.getcwd(),
        local_tmp_directory=os.getcwd(),
        port=22,
        mountpoint='/',
        connect_timeout=2,
        secure=False,
        implicit_TLS=False,
    ):
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.local_current_directory = local_current_directory
        self.local_tmp_directory = local_tmp_directory
        self.mountpoint = mountpoint
        self.__is_login = False
        self.conn_timeout = connect_timeout
        self.ssh_client = paramiko.SSHClient()
        self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.ssh_transport = None
        self.ssh_ftp = None

        self.ls_list = []
        if (self.local_tmp_directory[-1] == '/'):
            self.local_tmp_directory = self.local_tmp_directory[:-1]

        log1 = ('host:%s,username=%s, password=%s, port=%d' % (
            self.host,
            self.username,
            self.password,
            self.port,
        ))
        log2 = ('mountpoint:%s, conn_timeout=%d' % (
            self.mountpoint,
            self.conn_timeout,
        ))
        log3 = ('local_current_directory=%s' %
                (self.local_current_directory, ))
        log4 = ('local_tmp_directory=%s' % (self.local_tmp_directory, ))
        if __name__ != '__main__':

            if (orisolLog(level='info',
                          module_name=Module_Name,
                          message='log1-params:%s' % (log1)) == False):
                print('log1-', log1)
            if (orisolLog(level='info',
                          module_name=Module_Name,
                          message='log2-params:%s' % (log2)) == False):
                print('log2-', log2)
            if (orisolLog(level='info',
                          module_name=Module_Name,
                          message='log3-params:%s' % (log3)) == False):
                print('log3-', log3)
            if (orisolLog(level='info',
                          module_name=Module_Name,
                          message='log4-params:%s' % (log4)) == False):
                print('log4-', log4)
        else:
            print(log1)
            print(log2)
            print(log3)
            print(log4)

    def Is_timeout_reconnect(self):
        b_reconnect = not(self.is_remote_alive())
        return b_reconnect

    def remote_set_host(self, host):
        self.host = host

    def remote_set_username(self, username):
        self.username = username

    def remote_set_password(self, password):
        self.password = password

    def remote_set_port(self, port):
        self.port = int(port)

    def remote_set_mountpoint(self, mountpoint):
        self.mountpoint = mountpoint

    def remote_print_parameter(self):
        log1 = ('host:%s,\nusername=%s,\npassword=%s,\nport=%d\n' % (
            self.host,
            self.username,
            self.password,
            self.port,
        ))
        print(log1)

    def filename_parsing(self, str_list, str_idx, attr=None):
        _str = ''
        for idx, str_tmp in enumerate(str_list):
            if (idx > str_idx):
                _str += ' '
            if (idx >= str_idx):
                _str += str_tmp

        if attr is not None and attr == 'l':
            _str_link = _str.split('->')[0]
            _str = "".join(_str_link.rstrip().lstrip())

        return _str

    def remote_quit(self):
        if not self.__is_login:
            return
        try:
            if self.ssh_transport is not None:
                self.ssh_transport.close()
            if self.ssh_ftp is not None:
                self.ssh_ftp.close()
            self.access_status = -1
        except Exception as err:
            if __name__ != '__main__':
                orisolLog(level='error',
                          module_name=Module_Name,
                          message='remote_quit err:%s' % (err))
        finally:
            pass

    def remote_exec_command(self, cmd):
        status = -1
        try:
            stdin, stdout, stderr = self.ssh_client(cmd)
            output = stdout.read().decode().strip()
            errors = stderr.read().decode().strip()
            print('output:', output)
            print('errors:', errors)
            status = 0
        except paramiko.AuthenticationException as err:
            if __name__ != '__main__':
                orisolLog(level='error',
                          module_name=Module_Name,
                          message='exec err:%s' % (err))
            else:
                print(err)

        except paramiko.SSHException as e:
            # print(f"SSH error: {e}")
            if __name__ != '__main__':
                orisolLog(level='error',
                          module_name=Module_Name,
                          message='exec err:%s' % (err))
            else:
                print(err)

        except Exception as e:
            # print(f"An unexpected error occurred: {e}")
            if __name__ != '__main__':
                orisolLog(level='error',
                          module_name=Module_Name,
                          message='exec err:%s' % (err))
            else:
                print(err)

        finally:
            return status

    def remote_open_init(self, change_to_backup=False):
        status = -1
        msg = ''
        welcome = ''
        conn_msg = ''
        try:
            ret = self.ssh_client.connect(hostname=self.host,
                                          port=self.port,
                                          username=self.username,
                                          password=self.password,
                                          timeout=self.conn_timeout,
                                          allow_agent=False,
                                          look_for_keys=False)
            status = 0
        except Exception as e:
            msg = e

        if status == 0:
            # because, ui-filemanager click ftp button (in the left hand side), it's meaning to refresh ftp file list,
            # and back to original working path, ignore where ever the current
            # working path.
            self.ssh_transport = self.ssh_client.get_transport()
            self.ssh_transport.set_keepalive(60)
            self.ssh_ftp = self.ssh_client.open_sftp()
            welcome = self.ssh_transport.get_banner()

            self.__is_login = True
            if not change_to_backup:
                self.working_directory['remote'] = '/'
                self.working_directory['remote_backup'] = '/'

        # change to mountpoint
        if status == 0:
            self.access_status = 0
            if not change_to_backup:
                status, msg = self.remote_change_current_folder(
                    cwd=self.mountpoint)
                if status == 0:
                    self.working_directory['remote'] = self.ssh_ftp.getcwd()
                    self.working_directory[
                        'remote_backup'] = self.working_directory['remote']
            else:
                status, msg = self.remote_change_current_folder(
                    cwd=self.working_directory['remote_backup'])
                self.working_directory['remote'] = self.ssh_ftp.getcwd()

        return status, msg, welcome

    def is_remote_alive(self):
        if self.ssh_transport is not None:
            return self.ssh_transport.is_active()
        else:
            return False

    def remote_open(self, change_to_backup=False):
        status = -1
        msg = ''
        welcome = ''
        if self.is_remote_alive() == False:
            status, msg, welcome = self.remote_open_init()
        else:
            status = 0
        return status, msg, welcome

    def remote_modify_timestamp(self, dst_path):
        locale.setlocale(locale.LC_TIME, "en_US.UTF-8")
        current_time = int(time())
        new_times = (current_time, current_time)
        self.ssh_ftp.utime(dst_path, new_times)

    @synchronized
    def remote_change_current_folder(self, cwd):
        # if cwd[0] = '/'
        # e.g. cwd = '/pub/example'
        # --> tmp_cwd = /pub/example (absoulte path)
        # if cwd[0] != '/'
        # e.g. cwd = 'kkk'
        # --> tmp_cwd = working_directory['remote_backup'] + /kkk

        status = -1
        msg = ''
        tmp_cwd = ''
        if cwd is None:
            return -1, 'input argument failed'

        if cwd == '..':
            dir_token = os.path.split(self.working_directory['remote_backup'])
            tmp_cwd = dir_token[0]
        elif cwd == '/':
            tmp_cwd = self.mountpoint
        elif cwd is not None:
            tmp_cwd = os.path.join(self.working_directory['remote_backup'],
                                   cwd)

        try:
            msg = self.ssh_ftp.chdir(tmp_cwd)
            status = 0

        except (error_perm, socket.error) as err:
            if __name__ != '__main__':
                orisolLog(level='info',
                          module_name=Module_Name,
                          message='cwd err:%s' % (str(err)))
            else:
                print(err)
            msg = str(err)
        finally:
            if status == 0:
                self.working_directory['remote'] = self.ssh_ftp.getcwd()
                self.working_directory['remote_backup'] = tmp_cwd
        return status, msg

    @synchronized
    def remote_change_up_current_folder(self):
        # Change working directory to the parent of the current directory
        status = -1
        msg = ''
        try:
            current_path = self.ssh_ftp.getcwd()
            msg = self.ssh_ftp.chdir('..')

        except (error_perm, socket.error) as err:
            if __name__ != '__main__':
                orisolLog(
                    level='error',
                    module_name=Module_Name,
                    message='cdup error:%s' % (err))
            else:
                print('cdup error:', err)
                msg = str(err)
        finally:
            pass
        return status, msg

    @synchronized
    def remote_pwd(self):
        status = -1
        msg = ''
        if self.access_status == 0:
            try:
                msg = self.ssh_ftp.getcwd()
                status = 0
            except (error_perm, Exception, socket.error) as err:
                if __name__ != '__main__':
                    orisolLog(level='error',
                              module_name=Module_Name,
                              message='pwd err:%s' % (str(err)))
                else:
                    print(err)
                msg = str(err)
            finally:
                pass
        return status, msg

    @synchronized
    def remote_get_file_list(self, search_path=None):
        status = -1
        pwd_status = 0
        remote_list = []
        err_msg = ''

        if search_path is not None and search_path != '' and search_path[0] != '/':
            search_path = '/' + search_path[:]

        if search_path is None or search_path == '':
            search_path = self.working_directory['remote']

        if __name__ != '__main__':
            orisolLog(level='debug',
                      module_name=Module_Name,
                      message='get file list:%s' % (search_path))
        try:
            # -debug
            self.ls_list.clear()
            self.ls_list = self.ssh_ftp.listdir_attr(search_path)
            status = 0
        except Exception as err:
            err_msg = str(err)
            if __name__ != '__main__':
                orisolLog(
                    level='error',
                    module_name=Module_Name,
                    message='remote_get_file_list:%s' % (str(err)),
                )
            else:
                print('remote-list err:', err)

        finally:
            if status == 0 and pwd_status == 0:
                for line in self.ls_list:
                    file_attribute = {
                        'file_permissions': '',
                        'file_name': '',
                        'file_path': '',
                        'file_type': '',
                        'file_size': '',
                        'size_human': '',
                        'date': '',
                    }

                    file_permissions = str(line)[0:10]
                    if file_permissions[0] == 'd':
                        file_attribute['file_type'] = 'DIR'
                        # file_attribute['file_name'] = self.filename_parsing(file_tmp, 7)
                        file_attribute['file_name'] = line.filename
                        file_attribute['file_path'] = os.path.join(
                            search_path, file_attribute['file_name'])
                    elif file_permissions[0] == 'l':
                        file_attribute['file_type'] = 'SYMLINK'
                        # file_attribute['file_name'] = self.filename_parsing(file_tmp, 7, attr='l')
                        file_attribute['file_name'] = line.filename
                        file_attribute['file_path'] = os.path.join(
                            search_path, file_attribute['file_name'])
                    else:
                        # file_attribute['file_type'] = (os.path.splitext(file_tmp[7])[1])[1:].upper()
                        file_attribute['file_type'] = (os.path.splitext(
                            line.filename)[1])[1:].upper()
                        # file_attribute['file_name'] = self.filename_parsing(file_tmp, 7)
                        file_attribute['file_name'] = line.filename
                        file_attribute['file_path'] = os.path.join(
                            search_path, file_attribute['file_name'])
                    file_attribute['file_permissions'] = file_permissions
                    file_attribute['file_size'] = int(line.st_size)
                    file_attribute['size_human'] = pretty_size(
                        int(line.st_size))
                    mtime_datetime = datetime.fromtimestamp(line.st_mtime)
                    formatted_mtime = mtime_datetime.strftime(
                        "%Y/%m/%d %H:%M:%S")
                    file_attribute['date'] = formatted_mtime
                    remote_list.append(file_attribute)
                    sorted(remote_list, key=lambda x: ['file_name'])
                return status, remote_list
            else:
                return status, err_msg

    @synchronized
    def remote_mkdir(self, path_name):
        status = -1
        msg = ''
        try:
            msg = self.ssh_ftp.mkdir(path_name)
            status = 0
        except Exception as err:
            if __name__ != '__main__':
                orisolLog(level='info',
                          module_name=Module_Name,
                          message='mkdir err:%s' % (str(err)))
            else:
                print(err)
            msg = str(err)
        finally:
            pass
        return status, msg

    @synchronized
    def remote_rmdir(self, path_name):
        status = -1
        msg = ''
        try:
            msg = self.ssh_ftp.rmdir(path_name)
            status = 0
        except Exception as err:
            if __name__ != '__main__':
                orisolLog(level='info',
                          module_name=Module_Name,
                          message='rmdir err:%s' % (str(err)))
            else:
                msg = 'rm_path:%s, err:%s' % (path_name, str(err))
                print(msg)
        finally:
            pass
        return status, msg

    @synchronized
    def remote_rmfile(self, file_name):
        print('rmfile:', file_name)
        status = -1
        msg = ''
        try:
            msg = self.ssh_ftp.remove(file_name)
            status = 0
        except (error_perm, Exception, socket.error) as err:
            if __name__ != '__main__':
                orisolLog(level='info',
                          module_name=Module_Name,
                          message='rmfile err:%s' % (str(err)))
            else:
                print(err)
            msg = str(err)
        finally:
            pass
        return status, msg

    @synchronized
    def remote_upload(self,
                      src_filename,
                      dst_filename=None,
                      src_mount=None,
                      dst_mount=None):
        status = -1
        msg = 0
        bufsize = 1024
        local_filepath = ''
        remote_filepath = ''
        remote_dir = ''
        if src_mount is None:
            local_filepath = os.path.join(self.local_current_directory,
                                          src_filename)
        else:
            local_filepath = os.path.join(src_mount, src_filename)
        if not os.path.isfile(local_filepath):
            return -2, 'cannot find local file'
        if dst_mount is not None and dst_mount[0] != '/':
            dst_mount = '/' + dst_mount[:]
        if dst_mount is None and dst_filename is None:
            remote_filepath = os.path.join(
                self.working_directory['remote_backup'], src_filename)
            remote_dir = self.working_directory['remote_backup']
        elif dst_mount is None and dst_filename is not None:
            remote_filepath = os.path.join(
                self.working_directory['remote_backup'], dst_filename)
            remote_dir = self.working_directory['remote_backup']
        elif dst_mount is not None and dst_filename is None:
            remote_filepath = os.path.join(dst_mount, src_filename)
            remote_dir = dst_mount
        elif dst_mount is not None and dst_filename is not None:
            remote_filepath = os.path.join(dst_mount, dst_filename)
            remote_dir = dst_mount
        local_f = open(local_filepath, 'rb')
        try:
            self.ssh_ftp.putfo(fl=local_f,
                               remotepath=remote_filepath,
                               file_size=bufsize)
            status = 0
        except Exception as err:
            if __name__ != '__main__':
                orisolLog(level='info',
                          module_name=Module_Name,
                          message='remote_upload err:%s' % (str(err)))
            else:
                print('remote_upload err:', err)
            msg = str(err)
        finally:
            local_f.close()
            return status, msg

    @synchronized
    def remote_download(self,
                        src_filename,
                        dst_filename=None,
                        src_mount=None,
                        dst_mount=None):
        status = -1
        msg = ''
        bufsize = 1024
        local_filepath = ''
        remote_filepath = ''
        if src_mount is None:
            remote_filepath = os.path.join(
                self.working_directory['remote_backup'], src_filename)
        else:
            remote_filepath = os.path.join(src_mount, src_filename)
        if dst_mount is not None and dst_mount[0] != '/':
            dst_mount = '/' + dst_mount[:]
        if dst_mount is None and dst_filename is None:
            local_filepath = os.path.join(self.local_current_directory,
                                          src_filename)
        elif dst_mount is None and dst_filename is not None:
            local_filepath = os.path.join(self.local_current_directory,
                                          dst_filename)
        elif dst_mount is not None and dst_filename is None:
            local_filepath = os.path.join(dst_mount, src_filename)
        elif dst_mount is not None and dst_filename is not None:
            local_filepath = os.path.join(dst_mount, dst_filename)
        if not os.path.isdir(local_filepath) and os.path.exists(
                local_filepath):
            os.remove(local_filepath)
        local_f = open(local_filepath, 'wb')
        try:
            self.ssh_ftp.getfo(fl=local_f, remotepath=remote_filepath)
            status = 0
        except (error_perm, Exception, socket.error) as err:
            status = -1
            if __name__ != '__main__':
                orisolLog(level='info',
                          module_name=Module_Name,
                          message='remote_download err:%s' % (str(err)))
            else:
                print(err)
            msg = str(err)
        finally:
            local_f.close()
            if status == -1:
                if not os.path.isdir(local_filepath) and os.path.exists(
                        local_filepath):
                    os.remove(local_filepath)
            return status, msg

    @synchronized
    def remote_rename(self, old_name, new_name):
        status = -1
        msg = ''
        try:
            msg = self.ssh_ftp.posix_rename(oldpath=old_name, newpath=new_name)
            status = 0
        except (error_perm, Exception, socket.error) as err:
            if __name__ != '__main__':
                orisolLog(level='info',
                          module_name=Module_Name,
                          message='rename err:%s' % (str(err)))
            else:
                print(err)
            msg = str(err)
        finally:
            pass
        return status, msg

    def recursively_upload(self, src, dst):
        status = 0
        mass_transmmit_status = -1
        mass_transmmit_stop = 0
        err_msg = ''
        self.set_bulktag_info()

        if src[0] != '/':
            src = '/' + src[:]
        if dst[0] != '/':
            dst = '/' + dst[:]

        start_time = time()
        status, msg, welcome = self.remote_open()
        if status != 0:
            err_msg = msg
            escape_time = time() - start_time
            return mass_transmmit_status, escape_time, err_msg

        for iter in os.listdir(src):
            sub_src = os.path.join(src, iter)
            sub_dst = os.path.join(dst, iter)

            if os.path.isdir(sub_src):
                sub_dst_exist = False
                status, dst_items = self.remote_get_file_list(dst)
                for dst_item in dst_items:
                    if dst_item['file_type'] == 'DIR' and dst_item[
                            'file_path'] == sub_dst:
                        sub_dst_exist = True
                        break
                if not sub_dst_exist:
                    status = self.remote_mkdir(sub_dst)
                self.recursively_upload(sub_src, sub_dst)
            else:

                sub_src_tokens = os.path.split(sub_src)
                sub_dst_tokens = os.path.split(sub_dst)

                while (self.bulktag.get_retry_count() > 0
                       and mass_transmmit_stop == 0):
                    upload_status, msg = self.remote_upload(
                        src_filename=sub_src_tokens[1],
                        src_mount=sub_src_tokens[0],
                        dst_filename=sub_dst_tokens[1],
                        dst_mount=sub_dst_tokens[0])
                    if upload_status == -1:
                        status, msg, welcome = self.remote_open()
                        if status == 0:
                            self.bulktag.set_retry_count(count=3)
                        else:
                            retry_cnt = self.bulktag.get_retry_count() - 1
                            self.bulktag.set_retry_count(count=retry_cnt)
                            if retry_cnt == 0:
                                mass_transmmit_stop = 1
                    else:
                        self.remote_modify_timestamp(sub_dst)
                        break

        if mass_transmmit_stop == 0:
            mass_transmmit_status = 0
        escape_time = time() - start_time
        if mass_transmmit_status == 0:
            status, msg, welcome = self.remote_open()
        return mass_transmmit_status, escape_time, err_msg

    def recursively_download(self, src, dst):
        status = 0
        mass_transmmit_status = -1
        mass_transmmit_stop = 0
        err_msg = ''
        self.set_bulktag_info()

        if src[0] != '/':
            src = '/' + src[:]
        if dst[0] != '/':
            dst = '/' + dst[:]

        start_time = time()
        status, msg, welcome = self.remote_open()
        if status != 0:
            err_msg = msg
            escape_time = time() - start_time
            return mass_transmmit_status, escape_time, err_msg

        status, src_items = self.remote_get_file_list(src)
        for src_item in src_items:
            sub_src = src_item['file_path']
            sub_dst = os.path.join(dst,
                                   os.path.split(src_item['file_path'])[1])
            if src_item['file_type'] == 'DIR':
                if not os.path.isdir(sub_dst):
                    os.mkdir(sub_dst)
                self.recursively_download(sub_src, sub_dst)
            else:
                if os.path.exists(sub_dst):
                    os.remove(sub_dst)

                sub_src_tokens = os.path.split(sub_src)
                sub_dst_tokens = os.path.split(sub_dst)

                while (self.bulktag.get_retry_count() > 0
                       and mass_transmmit_stop == 0):
                    download_status, msg = self.remote_download(
                        src_filename=sub_src_tokens[1],
                        src_mount=sub_src_tokens[0],
                        dst_filename=sub_dst_tokens[1],
                        dst_mount=sub_dst_tokens[0])
                    if download_status == -1:
                        status, msg, welcome = self.remote_open()
                        if status == 0:
                            self.bulktag.set_retry_count(count=3)
                        else:
                            retry_cnt = self.bulktag.get_retry_count() - 1
                            self.bulktag.set_retry_count(count=retry_cnt)
                            if retry_cnt == 0:
                                mass_transmmit_stop = 1
                    else:
                        break

        if mass_transmmit_stop == 0:
            mass_transmmit_status = 0
        escape_time = time() - start_time
        if mass_transmmit_status == 0:
            status, msg, welcome = self.remote_open()
        return mass_transmmit_status, escape_time, err_msg

    def remote_path_exists(self, path):
        try:
            self.ssh_ftp.stat(path)
            return True
        except FileNotFoundError:
            return False

    def remote_file_info(self, remote_file_path):
        status = -1
        try:
            file_info = self.ssh_ftp.stat(remote_file_path)
            status = 0
        except IOError as e:
            status = -1
            print(f"Error getting file info: {e}")
        finally:
            # print('file_attributes:',file_attributes)
            file_attribute = {
                'file_permissions': '',
                'file_name': '',
                'file_path': '',
                'file_type': '',
                'file_size': '',
                'size_human': '',
                'date': '',
            }
            file_permissions = str(file_info)[0:10]
            if file_permissions[0] == 'd':
                file_attribute['file_type'] = 'DIR'
                file_attribute['file_name'] = os.path.split(remote_file_path)[
                    1]
                file_attribute['file_path'] = remote_file_path

            elif file_permissions[0] == 'l':
                file_attribute['file_type'] = 'SYMLINK'
                file_attribute['file_name'] = os.path.split(remote_file_path)[
                    1]
                file_attribute['file_path'] = remote_file_path

            else:
                file_attribute['file_name'] = os.path.split(remote_file_path)[
                    1]
                file_attribute['file_path'] = remote_file_path
                file_attribute['file_type'] = (os.path.splitext(
                    file_attribute['file_name'])[1])[1:].upper()

            file_attribute['file_permissions'] = file_permissions
            file_attribute['file_size'] = int(file_info.st_size)
            file_attribute['size_human'] = pretty_size(int(file_info.st_size))
            mtime_datetime = datetime.fromtimestamp(file_info.st_mtime)
            formatted_mtime = mtime_datetime.strftime("%Y/%m/%d %H:%M:%S")
            file_attribute['date'] = formatted_mtime
        return status, file_attribute

    def recursively_remove(self, dst):
        status = 0
        mass_transmmit_status = -1
        mass_transmmit_stop = 0
        err_msg = ''
        self.set_bulktag_info()
        if dst[0] != '/':
            dst = os.path.join(self.working_directory['remote_backup'], dst)
        start_time = time()
        status, msg, welcome = self.remote_open()
        if status != 0:
            err_msg = msg
            escape_time = time() - start_time
            return mass_transmmit_status, escape_time, err_msg

        status, dst_items = self.remote_get_file_list(dst)
        for dst_item in dst_items:
            sub_dst = dst_item['file_path']
            if dst_item['file_type'] == 'DIR':
                self.recursively_remove(sub_dst)
                if self.remote_path_exists(sub_dst):
                    self.remote_rmdir(sub_dst)
            else:
                while (self.bulktag.get_retry_count() > 0
                       and mass_transmmit_stop == 0):
                    remove_status, msg = self.remote_rmfile(sub_dst)
                    if remove_status == -1:
                        status, msg, welcome = self.remote_open()
                        if status == 0:
                            self.bulktag.set_retry_count(count=3)
                        else:
                            retry_cnt = self.bulktag.get_retry_count() - 1
                            self.bulktag.set_retry_count(count=retry_cnt)
                            if retry_cnt == 0:
                                mass_transmmit_stop = 1
                    else:
                        break

        if mass_transmmit_stop == 0:
            mass_transmmit_status = 0
            if dst != '/':
                self.remote_rmdir(dst)

        escape_time = time() - start_time
        if mass_transmmit_status == 0:
            status, msg, welcome = self.remote_open()
        return mass_transmmit_status, escape_time, err_msg

    def local_get_current_folder(self):
        return self.local_current_directory

    def search_local_file(self, search_path=None, ext_filter_list=None):
        filelist = []
        for root, dirs, files in os.walk(top=search_path, topdown=False):
            for file in files:
                if root == search_path:
                    if ext_filter_list is None:
                        filelist.append(file)
                    else:
                        filename, dot_ext = os.path.splitext(file)
                        ext = dot_ext[1:]
                        if ext in ext_filter_list:
                            filelist.append(filename)
        filelist.sort(reverse=False)
        return filelist

    def search_local_folder(self, search_path=None):
        folderlist = []
        for root, dirs, files in os.walk(top=search_path, topdown=False):
            for dir in dirs:
                if root == search_path:
                    folderlist.append(dir)
        folderlist.sort(reverse=False)
        return folderlist

    def local_get_file_list(self, search_path=None):
        status = -1
        local_list = []
        if search_path is None:
            search_path = self.local_current_directory

        if os.path.exists(search_path):
            status = 0
            files = self.search_local_file(search_path)
            folders = self.search_local_folder(search_path)
            for file in files:
                local_list.append(file)
            for folder in folders:
                local_list.append(folder + '/')
        return status, local_list

    def local_get_file_list_absolute_path(self, search_path):
        status = -1
        absolute_files = []
        if os.path.exists(search_path):
            status = 0
            files = self.search_local_file(search_path)
            for file in files:
                absolute_files.append(os.path.join(search_path, file))
        return status, absolute_files

    def local_change_currnet_folder(self, folder_name):
        status = -1
        if folder_name == '..':
            new_path = os.path.split(self.local_current_directory)[0]
        else:
            path_1, path_2 = os.path.split(folder_name)
            if path_1 == '':
                new_path = os.path.join(self.local_current_directory,
                                        folder_name)
            else:
                new_path = os.path.join(path_1, path_2)

        try:
            if os.path.exists(new_path):
                self.local_current_directory = new_path
                status = 0
            else:
                print('set_local_current_folder not exist')

        except (Exception, socket.error) as err:
            print('set_local_current_folder err:', err)

        finally:
            print('local new path:%s' % (self.local_get_current_folder()))
            return status

    def print_info_working_dir(self):
        print('-working_directory-info:', self.working_directory)

    def get_remote_mountpoint_info(self):
        mountpoint_info = {
            'ftp_remote_folder': self.working_directory['remote'],
            'ftp_remote_backup': self.working_directory['remote_backup'],
            'local_directory': self.local_current_directory,
            'ftp_localtemp_mountpoint': self.local_tmp_directory,
            'ftp_mountpoint': self.mountpoint,
        }
        return mountpoint_info

    @synchronized
    def set_bulktag_upload_info_cli(self, src_files, dst_mount):
        src_mount = ''
        file_list = []
        for file in src_files:
            src_mount, filename = os.path.split(file)
            file_list.append(filename)
        self.set_bulktag_info(src_mount=src_mount,
                              dst_mount=dst_mount,
                              src_filenamelist=file_list,
                              dst_filenamelist=file_list)

    @synchronized
    def set_bulktag_download_info_cli(self, src_files, dst_mount):
        src_mount = ''
        file_list = []
        for file in src_files:
            if file['file_type'] != 'DIR':
                src_mount, filename = os.path.split(file['file_path'])
                file_list.append(filename)
        self.set_bulktag_info(src_mount=src_mount,
                              dst_mount=dst_mount,
                              src_filenamelist=file_list,
                              dst_filenamelist=file_list)

    @synchronized
    def set_bulktag_info(
        self,
        dst_filenamelist=None,
        dst_mount=None,
        src_mount=None,
        src_filenamelist=None,
    ):
        self.bulktag.init_parameter(src_mount=src_mount,
                                    dst_mount=dst_mount,
                                    src_filename_list=src_filenamelist,
                                    dst_filename_list=dst_filenamelist)

    @synchronized
    def bulk_download(self):
        mass_transmmit_status = -1
        mass_transmmit_stop = 0
        err_msg = ''

        start_time = time()
        while (self.bulktag.get_retry_count() > 0
               and mass_transmmit_status == -1):
            status, msg, welcome = self.remote_open()
            if status == 0:
                self.bulktag.set_retry_count(count=3)
            else:
                retry_cnt = self.bulktag.get_retry_count() - 1
                self.bulktag.set_retry_count(count=retry_cnt)
            while (1):
                idx = self.bulktag.get_index()
                print('download idx:', idx)
                if idx >= self.bulktag.get_length_src_filelist():
                    mass_transmmit_status = 0
                    break

                status, msg = self.remote_download(
                    src_filename=self.bulktag.get_src_filename_by_index(),
                    src_mount=self.bulktag.get_src_mount(),
                    dst_mount=self.bulktag.get_dst_mount())
                if status == 0:
                    idx += 1
                    self.bulktag.set_index(index=idx)
                else:
                    # if msg[0] in self.negative_resp:
                    #     mass_transmmit_stop = 1
                    #     err_msg = msg
                    mass_transmmit_stop = 1
                    err_msg = msg
                    break

            if mass_transmmit_stop == 1:
                break

        escape_time = time() - start_time
        if mass_transmmit_status == 0:
            status, msg, welcome = self.remote_open()
        return mass_transmmit_status, escape_time, err_msg

    @synchronized
    def bulk_upload(self):
        mass_transmmit_status = -1
        mass_transmmit_stop = 0
        err_msg = ''

        start_time = time()
        while (self.bulktag.get_retry_count() > 0
               and mass_transmmit_status == -1):
            status, msg, welcome = self.remote_open()
            if status == 0:
                self.bulktag.set_retry_count(count=3)
            else:
                retry_cnt = self.bulktag.get_retry_count() - 1
                self.bulktag.set_retry_count(count=retry_cnt)
            while (1):
                idx = self.bulktag.get_index()
                if idx >= self.bulktag.get_length_src_filelist():
                    mass_transmmit_status = 0
                    break
                status, msg = self.remote_upload(
                    src_filename=self.bulktag.get_src_filename_by_index(),
                    src_mount=self.bulktag.get_src_mount(),
                    dst_mount=self.bulktag.get_dst_mount())
                if status == 0:
                    idx += 1
                    self.bulktag.set_index(index=idx)
                else:
                    # if msg[0] in self.negative_resp:
                    #     mass_transmmit_stop = 1
                    #     err_msg = msg
                    mass_transmmit_stop = 1
                    err_msg = msg
                    break

            if mass_transmmit_stop == 1:
                break

        escape_time = time() - start_time
        if mass_transmmit_status == 0:
            status, msg, welcome = self.remote_open()
        return mass_transmmit_status, escape_time, err_msg

    @synchronized
    def bulk_delete(self):
        mass_transmmit_status = -1
        mass_transmmit_stop = 0
        err_msg = ''

        start_time = time()
        while (self.bulktag.get_retry_count() > 0
               and mass_transmmit_status == -1):
            status, msg, welcome = self.remote_open()
            if status == 0:
                self.bulktag.set_retry_count(count=3)
            else:
                retry_cnt = self.bulktag.get_retry_count() - 1
                self.bulktag.set_retry_count(count=retry_cnt)
            while (1):
                idx = self.bulktag.get_index()
                if idx >= self.bulktag.get_length_dst_filelist():
                    mass_transmmit_status = 0
                    break
                dst_filepath = os.path.join(
                    self.bulktag.get_dst_mount(),
                    self.bulktag.get_dst_filename_by_index())
                status, msg = self.remote_rmfile(dst_filepath)
                if status == 0:
                    idx += 1
                    self.bulktag.set_index(index=idx)
                else:
                    # if msg[0] in self.negative_resp:
                    #     mass_transmmit_stop = 1
                    #     err_msg = msg
                    mass_transmmit_stop = 1
                    err_msg = msg
                    break

            if mass_transmmit_stop == 1:
                break

        escape_time = time() - start_time
        if mass_transmmit_status == 0:
            status, msg, welcome = self.remote_open()
        return mass_transmmit_status, escape_time, err_msg

    def gen_relative_local_directory(self, local_mountpoint, filepath):
        filepath_tokens = os.path.split(filepath)[0].split('/')
        tmp_path = local_mountpoint
        for token in filepath_tokens:
            if token == '':
                continue
            tmp_path = os.path.join(tmp_path, token)
            if os.path.exists(tmp_path):
                continue
            else:
                os.mkdir(tmp_path)

    @synchronized
    def remote_relative_download(self, src_filepaths,
                                 dst_filepaths=None,
                                 src_mountpoint=None,
                                 dst_mountpoint=None):
        status = -1
        msg = ''
        bufsize = 1024
        remote_mountpoint = ''
        remote_filepath = ''

        local_mountpoint = ''
        local_filepath = ''

        if src_mountpoint is None:
            remote_mountpoint = self.mountpoint
        else:
            remote_mountpoint = src_mountpoint

        if dst_mountpoint is None:
            local_mountpoint = self.local_tmp_directory
        else:
            local_mountpoint = dst_mountpoint

        for src_filepath in src_filepaths:
            remote_filepath = os.path.join(remote_mountpoint, src_filepath)
            status = self.remote_path_exists(remote_filepath)

            if status:
                self.gen_relative_local_directory(
                    local_mountpoint, src_filepath)
                local_filepath = os.path.join(local_mountpoint, src_filepath)
                if not os.path.isdir(
                        local_filepath) and os.path.exists(local_filepath):
                    os.remove(local_filepath)

                local_f = open(local_filepath, 'wb')
                try:
                    self.ssh_ftp.getfo(fl=local_f, remotepath=remote_filepath)
                    status = 0
                except (error_perm, Exception, socket.error) as err:
                    status = -1
                    if __name__ != '__main__':
                        orisolLog(
                            level='info',
                            module_name=Module_Name,
                            message='remote_download err:%s' %
                            (str(err)))
                    else:
                        print(err)
                    msg = str(err)
                finally:
                    local_f.close()
                    if status == -1:
                        if not os.path.isdir(local_filepath) and os.path.exists(
                                local_filepath):
                            os.remove(local_filepath)
        return status, msg

    def gen_relative_remote_directory(self, mountpoint, filepath):
        filepath_tokens = os.path.split(filepath)[0].split('/')
        tmp_path = mountpoint
        for token in filepath_tokens:
            if token == '':
                continue
            tmp_path = os.path.join(tmp_path, token)
            status, info = self.remote_get_file_list(tmp_path)
            if status == 0:
                continue
            else:
                status, msg = self.remote_mkdir(tmp_path)
                if status == -1:
                    if __name__ != '__main__':
                        orisolLog(
                            level='info',
                            module_name=Module_Name,
                            message='remote_gen_relative_directory err:%s' %
                            (str(msg)))
                    else:
                        print(msg)

    @synchronized
    def remote_relative_upload(self, src_filepaths,
                               dst_filepaths=None,
                               src_mountpoint=None,
                               dst_mountpoint=None):
        status = -1
        msg = ''
        bufsize = 1024
        remote_mountpoint = ''
        remote_filepath = ''

        local_mountpoint = ''
        local_filepath = ''

        if src_mountpoint is None:
            local_mountpoint = self.local_tmp_directory
        else:
            local_mountpoint = src_mountpoint

        if dst_mountpoint is None:
            remote_mountpoint = self.mountpoint
        else:
            remote_mountpoint = dst_mountpoint

        for src_filepath in src_filepaths:
            local_filepath = os.path.join(local_mountpoint, src_filepath)
            if os.path.isdir(local_filepath):
                continue
            elif not os.path.exists(local_filepath):
                continue
            self.gen_relative_remote_directory(remote_mountpoint, src_filepath)
            remote_filepath = os.path.join(remote_mountpoint, src_filepath)
            local_f = open(local_filepath, 'rb')
            try:
                self.ssh_ftp.putfo(fl=local_f,
                                   remotepath=remote_filepath,
                                   file_size=bufsize)
                status = 0
            except (error_perm, Exception, socket.error) as err:
                if __name__ != '__main__':
                    orisolLog(
                        level='info',
                        module_name=Module_Name,
                        message='remote_relative_upload err:%s' %
                        (str(err)))
                else:
                    print('remote_relative_upload err:', err)
                msg = str(err)
            finally:
                local_f.close()
        return status, msg


def cli_help():
    help_doc = ''
    help_doc += '=====   command line help list   =====' + os.linesep
    help_doc += ' > "open"           : open and login ftp server' + os.linesep
    help_doc += ' > "ls"             : lists directory contents of files and directories' + os.linesep
    help_doc += ' > "cd"             : change current directory' + os.linesep
    help_doc += ' > "pwd"            : print current working directory' + os.linesep
    help_doc += ' > "mkdir"          : create a new directory' + os.linesep
    help_doc += ' > "rmdir"          : remove a directory' + os.linesep
    help_doc += ' > "rm"             : remove a file' + os.linesep
    help_doc += ' > "cdup"           : change wroking direcotry to parent directory' + os.linesep
    help_doc += ' > "put"            : upload file from local to remote' + os.linesep
    help_doc += ' > "get"            : download file from remote to local' + os.linesep
    help_doc += ' > "recur-upload"   : upload file and directory from local to remote recursively' + os.linesep
    help_doc += ' > "recur-download" : download file and directory from remote to local recursively' + os.linesep
    help_doc += ' > "recur-remove"   : delete file and directory from remote recursively' + os.linesep
    help_doc += '=====   end of command line help list   ====='
    print(help_doc)


def ssh_cli():
    sftp_client_cli = SSH_Ftp_Client()
    sftp_client_cli.init_parameter(
        host='172.16.13.12',
        username='TW-EO-M01',
        password='Pccgroup!969!',
        connect_timeout=6,
        mountpoint='/TW-EO-FTP/UHF/wade_1',
        port=2141
    )
    # sftp_client_cli.init_parameter(
    #     host='test.rebex.net',
    #     username='demo',
    #     password='password',
    #     connect_timeout=6,
    #     mountpoint='/',
    #     port=22
    # )
    # sftp_client_cli.init_parameter(host='192.168.0.103',
    #                                username='khoo',
    #                                password='khoo',
    #                                connect_timeout=6,
    #                                mountpoint='/',
    #                                port=22)
    while True:
        try:
            cli_input = input('>> ')
            cli_input_list = cli_input.split(' ')

            if len(cli_input_list) > 0:
                if cli_input_list[0] == 'q':
                    sftp_client_cli.remote_quit()
                    break

                elif cli_input_list[0] == 'help':
                    pass
                    cli_help()

                elif cli_input_list[0] == 'host':
                    if len(cli_input_list) > 1:
                        sftp_client_cli.remote_set_host(cli_input_list[1])

                elif cli_input_list[0] == 'user':
                    if len(cli_input_list) > 1:
                        sftp_client_cli.remote_set_username(cli_input_list[1])

                elif cli_input_list[0] == 'pass':
                    if len(cli_input_list) > 1:
                        sftp_client_cli.remote_set_password(cli_input_list[1])

                elif cli_input_list[0] == 'port':
                    if len(cli_input_list) > 1:
                        sftp_client_cli.remote_set_port(cli_input_list[1])

                elif cli_input_list[0] == 'mount':
                    if len(cli_input_list) > 1:
                        sftp_client_cli.remote_set_mountpoint(
                            cli_input_list[1])

                elif cli_input_list[0] == 'show-para':
                    sftp_client_cli.remote_print_parameter()

                elif cli_input_list[0] == 'open':
                    status, msg, welcome = sftp_client_cli.remote_open_init()
                    log = 'status:%d, msg:%s, welcome:%s' % (status, msg,
                                                             welcome)
                    print(log)

                elif cli_input_list[0] == 'quit':
                    sftp_client_cli.remote_quit()

                elif cli_input_list[0] == 'is-alive':
                    status = sftp_client_cli.is_remote_alive()
                    print('is_alive:', status)

                elif cli_input_list[0] == 'cd':
                    if len(cli_input_list) < 2:
                        print('cli- wrong command, try again:', cli_input_list)
                    else:
                        status, msg = sftp_client_cli.remote_change_current_folder(
                            cwd=cli_input_list[1])
                        log = 'CWD: status:%d, msg:%s' % (status, msg)
                        print(log)

                elif cli_input_list[0] == 'mkdir':
                    if len(cli_input_list) < 2:
                        print('cli- wrong command, try again:', cli_input_list)
                    else:
                        status = sftp_client_cli.remote_mkdir(
                            cli_input_list[1])

                elif cli_input_list[0] == 'rmdir':
                    if len(cli_input_list) < 2:
                        print('cli- wrong command, try again:', cli_input_list)
                    else:
                        status, msg = sftp_client_cli.remote_rmdir(
                            cli_input_list[1])
                        if status != 0:
                            print(msg)

                elif cli_input_list[0] == 'rm':
                    if len(cli_input_list) < 2:
                        print('cli- wrong command, try again:', cli_input_list)
                    else:
                        status = sftp_client_cli.remote_rmfile(
                            cli_input_list[1])

                elif cli_input_list[0] == 'ls':
                    if len(cli_input_list) > 1:
                        status, files = sftp_client_cli.remote_get_file_list(
                            cli_input_list[1])
                    else:
                        status, files = sftp_client_cli.remote_get_file_list()
                    if status == 0:
                        print(
                            '-----------------------------------------------------------------'
                        )
                        for file in files:
                            file_permissions = file['file_permissions']
                            file_name = file['file_name']
                            file_path = file['file_path']
                            file_type = file['file_type']
                            file_size_human = file['size_human']
                            file_date = file['date']
                            print(
                                f' {file_permissions:<10}  {file_name:<40}  {file_path:<60}  {file_type:<5}  {file_size_human:<5}  {file_date:<10} '
                            )
                        print('length of filelist:', len(files))

                elif cli_input_list[0] == 'fls':
                    if len(cli_input_list) == 2:
                        status, file_attr = sftp_client_cli.remote_file_info(
                            cli_input_list[1])
                        print(status, file_attr)

                elif cli_input_list[0] == 'pwd':
                    status, remote_pwd = sftp_client_cli.remote_pwd()
                    log = 'status=%d, remote_pwd:%s' % (status, remote_pwd)
                    print(log)

                elif cli_input_list[0] == 'mv':
                    if len(cli_input_list) < 3:
                        print('cli- wrong command, try again:', cli_input_list)
                    else:
                        status, msg = sftp_client_cli.remote_rename(
                            cli_input_list[1], cli_input_list[2])
                        print('renanme log:%d, msg:%s' % (status, msg))

                elif cli_input_list[0] == 'put':
                    if len(cli_input_list) < 2:
                        print('cli- wrong command, try again:', cli_input_list)
                    else:
                        # 1. put <filename>
                        # -> copy local current folder filename to remote current folder
                        # -> and the same filename in remote current folder
                        if len(cli_input_list) == 2:
                            if os.path.split(cli_input_list[1])[0] == '':
                                status = sftp_client_cli.remote_upload(
                                    cli_input_list[1])
                                print('status:', status)

                            # 1. put <abs_path/filename>
                            # -> copy local absolute path filename to remote current folder
                            # -> and the same filename in remote current folder
                            else:
                                local_mount, filename = os.path.split(
                                    cli_input_list[1])
                                status = sftp_client_cli.remote_upload(
                                    filename, src_mount=local_mount)
                                print('status:', status)

                        elif len(cli_input_list) == 3:
                            # combo: file1       file2
                            #        xx/file1    yy/
                            #                    yy/file2
                            #        xx/ - (not exists)
                            #
                            # 1. put <file1>     <file2>
                            # 2. put <file1>     <yy/>
                            # 3. put <file1>     <yy/file2>
                            # 4. put <xx/file1>  <file2>
                            # 5. put <xx/file1>  <yy/>
                            # 6. put <xx/file1>  <yy/file2>

                            local_mount, local_filename = os.path.split(
                                cli_input_list[1])
                            remote_mount, remote_filename = os.path.split(
                                cli_input_list[2])
                            if local_filename == '':
                                print('local_filename not exist')
                                continue
                            else:
                                if local_mount == '' and remote_mount == '':
                                    status = sftp_client_cli.remote_upload(
                                        src_filename=local_filename,
                                        dst_filename=remote_filename)
                                    print('condition(1), status:', status)
                                elif (local_mount == '' and remote_mount != ''
                                      and remote_filename == ''):
                                    status = sftp_client_cli.remote_upload(
                                        src_filename=local_filename,
                                        dst_mount=remote_mount)
                                    print('condition(2), status:', status)
                                elif (local_mount == '' and remote_mount != ''
                                      and remote_filename != ''):
                                    status = sftp_client_cli.remote_upload(
                                        src_filename=local_filename,
                                        dst_mount=remote_mount,
                                        dst_filename=remote_filename,
                                    )
                                    print('condition(3), status:', status)

                                elif (local_mount != '' and remote_mount == ''
                                      and remote_filename != ''):
                                    status = sftp_client_cli.remote_upload(
                                        src_filename=local_filename,
                                        src_mount=local_mount,
                                        dst_filename=remote_filename,
                                    )
                                    print('condition(4), status:', status)

                                elif (local_mount != '' and remote_mount != ''
                                      and remote_filename == ''):
                                    status = sftp_client_cli.remote_upload(
                                        src_filename=local_filename,
                                        src_mount=local_mount,
                                        dst_mount=remote_mount,
                                    )
                                    print('condition(5), status:', status)

                                elif (local_mount != '' and remote_mount != ''
                                      and remote_filename != ''):
                                    status = sftp_client_cli.remote_upload(
                                        src_filename=local_filename,
                                        src_mount=local_mount,
                                        dst_mount=remote_mount,
                                        dst_filename=remote_filename,
                                    )
                                    print('condition(6), status:', status)
                elif cli_input_list[0] == 'get':
                    if len(cli_input_list) < 2:
                        print('cli- wrong command, try again:', cli_input_list)
                    else:
                        # 1. get <filename>
                        # -> copy remote current folder filename to local current folder
                        # -> and the same filename in remote current folder
                        if len(cli_input_list) == 2:
                            if os.path.split(cli_input_list[1])[0] == '':
                                status = sftp_client_cli.remote_download(
                                    cli_input_list[1])
                                print('status:', status)

                        elif len(cli_input_list) == 3:
                            # combo: file1       file2
                            #        xx/file1    yy/
                            #                    yy/file2
                            #        xx/ - (not exists)
                            #
                            #        remote       local
                            # 1. get <file2>     <file1>
                            # 2. get <file2>     <xx/>
                            # 3. get <file2>     <xx/file1>
                            # 4. get <yy/file2>  <file2>
                            # 5. get <yy/file2>  <xx/>
                            # 6. get <yy/file2>  <yy/file1>

                            # local_mount,local_filename = os.path.split(cli_input_list[1])
                            # remote_mount,remote_filename = os.path.split(cli_input_list[2])
                            # ftp_download(self,src_filename,dst_filename=None,src_mount=None,dst_mount=None):
                            remote_mount, remote_filename = os.path.split(
                                cli_input_list[1])
                            if os.path.isdir(cli_input_list[2]):
                                local_mount = cli_input_list[2]
                                local_filename = ''
                            else:
                                local_mount, local_filename = os.path.split(
                                    cli_input_list[2])
                                if (local_mount != '' and
                                        os.path.isdir(local_mount) != True):
                                    print(
                                        '-2 cli- wrong command, try again:',
                                        cli_input_list,
                                    )
                                    continue

                            if remote_filename == '':
                                print('remote filename not exist')
                                continue
                            else:
                                if local_mount == '' and remote_mount == '':
                                    status = sftp_client_cli.remote_download(
                                        src_filename=remote_filename,
                                        dst_filename=local_filename,
                                    )
                                    print('condition(1), status:', status)
                                elif (local_mount != '' and remote_mount == ''
                                      and local_filename == ''):
                                    status = sftp_client_cli.remote_download(
                                        src_filename=remote_filename,
                                        dst_mount=local_mount,
                                    )
                                    print('condition(2), status:', status)
                                elif (local_mount != '' and remote_mount == ''
                                      and local_filename != ''):
                                    status = sftp_client_cli.remote_download(
                                        src_filename=remote_filename,
                                        dst_mount=local_mount,
                                        dst_filename=local_filename,
                                    )
                                    print('condition(3), status:', status)

                                elif (local_mount == '' and remote_mount != ''
                                      and local_filename != ''):
                                    status = sftp_client_cli.remote_download(
                                        src_filename=remote_filename,
                                        src_mount=remote_mount,
                                        dst_filename=local_filename,
                                    )
                                    print('condition(4), status:', status)

                                elif (local_mount != '' and remote_mount != ''
                                      and local_filename == ''):
                                    status = sftp_client_cli.remote_download(
                                        src_filename=remote_filename,
                                        src_mount=remote_mount,
                                        dst_mount=local_mount,
                                        dst_filename=remote_filename,
                                    )
                                    print('condition(5), status:', status)

                                elif (local_mount != '' and remote_mount != ''
                                      and local_filename != ''):
                                    status = sftp_client_cli.remote_download(
                                        src_filename=remote_filename,
                                        src_mount=remote_mount,
                                        dst_mount=local_mount,
                                        dst_filename=local_filename,
                                    )
                                    print('condition(6), status:', status)

                elif cli_input_list[0] == 'mass-download':
                    # usage:
                    # mass-download src_remote_abs_path dst_local_abs_path
                    if len(cli_input_list) == 3:
                        status, files = sftp_client_cli.remote_get_file_list(
                            cli_input_list[1])
                        if status == 0:
                            sftp_client_cli.set_bulktag_download_info_cli(
                                src_files=files, dst_mount=cli_input_list[2])
                            download_status, escape, msg = sftp_client_cli.bulk_download(
                            )
                            print('download_status:', download_status, escape,
                                  msg)
                    else:
                        log = 'cli cmd fault:%s' % (cli_input_list)
                        print(log)

                elif cli_input_list[0] == 'mass-upload':
                    # usage:
                    # mass-upload src_local_abs_path dst_remote_abs_path
                    if len(cli_input_list) == 3:
                        status, src_files = sftp_client_cli.local_get_file_list_absolute_path(
                            search_path=cli_input_list[1])
                        if status == 0:
                            sftp_client_cli.set_bulktag_upload_info_cli(
                                src_files=src_files,
                                dst_mount=cli_input_list[2])
                            upload_status, escape, msg = sftp_client_cli.bulk_upload(
                            )
                            print('upload_status:', upload_status, escape, msg)

                    else:
                        log = 'cli cmd fault:%s' % (cli_input_list)
                        print(log)

                elif cli_input_list[0] == 'rm-all':
                    # cwd to target directory then rm-all
                    if len(cli_input_list) < 2:
                        status, files = sftp_client_cli.remote_get_file_list()
                        if status == 0:
                            filelist = []
                            for file in files:
                                if file['file_type'] == 'DIR':
                                    continue
                                else:
                                    filelist.append(file['file_path'])
                            for file in filelist:
                                status = sftp_client_cli.remote_rmfile(file)
                    else:
                        status, files = sftp_client_cli.remote_get_file_list(
                            cli_input_list[1])
                        if status == 0:
                            filelist = []
                            for file in files:
                                if file['file_type'] == 'DIR':
                                    continue
                                else:
                                    filelist.append(file['file_path'])
                            for file in filelist:
                                status = sftp_client_cli.remote_rmfile(file)

                elif cli_input_list[0] == 'recur-upload':
                    if len(cli_input_list) == 3:
                        sftp_client_cli.recursively_upload(
                            cli_input_list[1], cli_input_list[2])
                elif cli_input_list[0] == 'recur-download':
                    if len(cli_input_list) == 3:
                        sftp_client_cli.recursively_download(
                            cli_input_list[1], cli_input_list[2])
                elif cli_input_list[0] == 'recur-remove':
                    if len(cli_input_list) == 2:
                        sftp_client_cli.recursively_remove(cli_input_list[1])

                # ---local ----------------------------------------------------
                elif cli_input_list[0] == 'lpwd':
                    local_pwd = sftp_client_cli.local_get_current_folder()
                    print('local current folder:%s' % (local_pwd))
                elif cli_input_list[0] == 'lls':
                    if len(cli_input_list) > 1:
                        status, local_filelist = sftp_client_cli.local_get_file_list(
                            search_path=cli_input_list[1])
                    else:
                        status, local_filelist = sftp_client_cli.local_get_file_list(
                        )
                    if status == 0:
                        print(local_filelist)
                elif cli_input_list[0] == 'lcd':
                    if len(cli_input_list) < 2:
                        print('cli- wrong command, try again:', cli_input_list)
                    else:
                        status = sftp_client_cli.local_change_currnet_folder(
                            cli_input_list[1])

                else:
                    print('cli- wrong command, try again')
            else:
                pass
        except KeyboardInterrupt:
            print('key interrupt occur !!!')
            break


def main():
    ssh_cli()
    # cli()
    sys.exit(0)


if __name__ == '__main__':
    main()
