import sys
from time import sleep, time
import json
import os
from io import StringIO
import ftplib
import socket
import ssl
from ftplib import error_perm
import threading
import shutil
import math
import json
from ssl import SSLSocket
from datetime import datetime
import locale


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

Module_Name = 'FtpClient'

g_conn_detection_event = threading.Event()
g_conn_detection_mutex = threading.Lock()


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


class Capturing(list):
    def __enter__(self):
        self._stdout = sys.stdout
        sys.stdout = self._stringio = StringIO()
        return self

    def __exit__(self, *args):
        self.extend(self._stringio.getvalue().splitlines())
        del self._stringio
        sys.stdout = self._stdout


class BulkTag():
    retry_count = 3
    src_mount = ''
    dst_mount = ''
    src_filename_list = []
    dst_filename_list = []
    bulk_index = 0

    def init_parameter(self, dst_mount=None, dst_filename_list=None,
                       src_mount=None, src_filename_list=None):
        self.src_filename_list.clear()
        self.dst_filename_list.clear()
        self.bulk_index = 0

        if src_mount != None:
            self.src_mount = src_mount
        if dst_mount != None:
            self.dst_mount = dst_mount

        if src_filename_list != None:
            self.src_filename_list = src_filename_list.copy()
        if dst_filename_list != None:
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

class ReusedSslSocket(SSLSocket):
    def unwrap(self):
        pass

class FtpClient(ftplib.FTP_TLS):
    # class FtpClient(ftplib.FTP):
    # host = '10.92.2.253'
    # username = 'Orisoltest'
    # password = 'Orisol1234'

    # host = 'demo.wftpserver.com'
    # username = 'demo'
    # password = 'demo'

    # host = 'test.rebex.net'
    # username = 'demo'
    # password = 'password'
    # port = 21, 990

    host = ''
    username = ''
    password = ''
    port = 21
    debug_level = 1
    conn_timeout = 5

    secure = True
    implicit_TLS = False

    remote_pwd_shadow = '/'
    working_directory = {'remote': '/',
                         'remote_backup': '/'}
    __is_login = False
    local_current_directory = os.getcwd()
    local_tmp_directory = os.getcwd()
    continuous_file_numbers = 128
    access_status = -1
    access_timestamp = time()
    access_diff = 10
    negative_resp = ['4', '5', '6']
    bulktag = BulkTag()

    def __new__(cls, *args, **kw):
        if not hasattr(cls, '_instance'):
            cls._instance = super(FtpClient, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        super(FtpClient, self).__init__()
        self.set_debuglevel(self.debug_level)
        self.set_pasv(True)

    def init_parameter(self, host,
                       username='anonymous', password='anonymous',
                       local_current_directory=os.getcwd(),
                       local_tmp_directory=os.getcwd(),
                       port=21,
                       secure=True,
                       implicit_TLS=False,
                       mountpoint='/',
                       connect_timeout=5,
                       ):
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.local_current_directory = local_current_directory
        self.local_tmp_directory = local_tmp_directory
        self.implicit_TLS = implicit_TLS
        self.secure = secure
        self.mountpoint = mountpoint
        self.__is_login = False
        self.conn_timeout = connect_timeout
        self.ls_list = []

        if (self.local_tmp_directory[-1] == '/'):
            self.local_tmp_directory = self.local_tmp_directory[:-1]

        log1 = (
            'host:%s,username=%s, password=%s, port=%d, implicit_TLS=%d, secure=%d'
            % (
                self.host,
                self.username,
                self.password,
                self.port,
                self.implicit_TLS,
                self.secure,
            )
        )
        log2 = (
            'mountpoint:%s, conn_timeout=%d'
            % (
                self.mountpoint,
                self.conn_timeout,
            )
        )
        log3 = (
            'local_current_directory=%s'
            % (
                self.local_current_directory,
            )
        )
        log4 = (
            'local_tmp_directory=%s'
            % (
                self.local_tmp_directory,
            )
        )
        if __name__ != '__main__':

            if (orisolLog(level='info', module_name=Module_Name, message='log1-params:%s' % (log1)) == False):
                print('log1-', log1)
            if (orisolLog(level='info', module_name=Module_Name, message='log2-params:%s' % (log2)) == False):
                print('log2-', log2)
            if (orisolLog(level='info', module_name=Module_Name, message='log3-params:%s' % (log3)) == False):
                print('log3-', log3)
            if (orisolLog(level='info', module_name=Module_Name, message='log4-params:%s' % (log4)) == False):
                print('log4-', log4)
        else:
            print(log1)
            print(log2)
            print(log3)
            print(log4)

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
        log1 = (
            'host:%s,\nusername=%s,\npassword=%s,\nport=%d,\nimplicit_TLS=%d, secure=%d'
            % (
                self.host,
                self.username,
                self.password,
                self.port,
                self.implicit_TLS,
                self.secure,
            )
        )
        print(log1)

    def filename_parsing(self, str_list, str_idx, attr=None):
        _str = ''
        for idx, str_tmp in enumerate(str_list):
            if (idx > str_idx):
                _str += ' '
            if (idx >= str_idx):
                _str += str_tmp

        if attr != None and attr == 'l':
            _str_link = _str.split('->')[0]
            _str = "".join(_str_link.rstrip().lstrip())

        return _str

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self,cmd,rest)
        if self._prot_p:
            conn = self.context.wrap_socket(conn,
                server_hostname=self.host,
                session=self.sock.session)
            # conn.__class__=ReusedSslSocket
        return conn, size

    def remote_connect(self):
        self.access_status = -1
        status = -1
        msg = ''

        if self.implicit_TLS == True and self.secure == True:
            try:
                self.sock = socket.create_connection(
                    (self.host, self.port), self.timeout)
                self.af = self.sock.family
                self.sock = ssl.wrap_socket(
                    self.sock,
                    self.keyfile,
                    self.certfile,
                    ssl_version=ssl.PROTOCOL_TLSv1,)
                self.file = self.sock.makefile('r')
                msg = self.getresp()
                if __name__ != '__main__':
                    orisolLog(level='info', module_name=Module_Name,
                              message='remote connect:%s' % (msg),)
                status = 0
            except (error_perm, socket.error) as err:
                if __name__ != '__main__':
                    orisolLog(level='error', module_name=Module_Name,
                              message='remote connect:%s' % (str(err)),)
                else:
                    print('error:', err)
                msg = str(err)

            finally:
                pass

        else:
            try:
                msg = self.connect(
                    host=self.host, timeout=self.conn_timeout, port=self.port)
                if __name__ != '__main__':
                    orisolLog(level='info', module_name=Module_Name,
                              message='remote connect:%s' % (msg),)
                status = 0
            except (Exception, socket.error) as err:
                if __name__ != '__main__':
                    orisolLog(level='error', module_name=Module_Name,
                              message='remote connect:%s' % (str(err)),)
                else:
                    print('connect error:', err)
                msg = str(err)

            finally:
                pass

        return status, msg

    def remote_login(self):
        status = -1
        try:
            # login - secure = False (FTP)
            # login - secure = True (FTP_TLS)
            msg = self.login(user=self.username,
                             passwd=self.password, secure=self.secure)
            if __name__ != '__main__':
                orisolLog(
                    level='info',
                    module_name=Module_Name,
                    message='remote login:%s' % (msg),)

            self.access_status = 0
            self.access_timestamp = time()
            status = 0
        except (error_perm, socket.error) as err:
            if __name__ != '__main__':
                orisolLog(
                    level='error',
                    module_name=Module_Name,
                    message='remote login:%s' % (str(err),))
            else:
                print('remote_login err:', str(err))
            msg = str(err)
        finally:
            pass
        return status, msg

    def remote_auth(self):
        try:
            ret = self.auth()
        except (error_perm, socket.error) as err:
            if __name__ != '__main__':
                orisolLog(
                    level='error',
                    module_name=Module_Name,
                    message='AUTH:%s' % (str(err)),)
            else:
                print('AUTH-err:', str(err))

    def remote_prot_p(self):
        try:
            ret = self.prot_p()
        except (error_perm, socket.error) as err:
            if __name__ != '__main__':
                orisolLog(
                    level='error',
                    module_name=Module_Name,
                    message='PBSZ:%s' % (str(err)),)
            else:
                print('PBSZ prot_p-err:', str(err))

    def remote_extension_support(self):
        status = -1
        try:
            str_resp = self.sendcmd('FEAT').split('\n')
            str_resp = [s.strip() for s in str_resp]
            if __name__ != '__main__':
                orisolLog(
                    level='info',
                    module_name=Module_Name,
                    message='extension:%s' % (str(str_resp),))
            else:
                print('support extions:', str(str_resp))

            if any("UTF8" in s for s in str_resp):
                self.encoding = 'utf-8'  # ftplib encoding (Latin-1 -> UTF-8)
                self.sendcmd('OPTS UTF8 ON')

            if any("CLNT" in s for s in str_resp):
                self.sendcmd('CLNT ORISOL-TW')
            self.remote_prot_p()

            status = 0

        except (error_perm, socket.error) as err:
            if __name__ != '__main__':
                orisolLog(
                    level='error',
                    module_name=Module_Name,
                    message='extension_support err:%s' % (str(err)),)
            else:
                print('extension_support err:', str(err))
        return status

    def remote_open(self, change_to_backup=False):
        status = -1
        msg = ''
        welcome = ''
        conn_msg = ''
        status, conn_msg = self.remote_connect()
        if status == 0:
            if self.secure == True and self.implicit_TLS == False:
                #self.remote_prot_p()
                pass
            status, msg = self.remote_login()
            status = self.remote_extension_support()

        # because, ui-filemanager click ftp button (in the left hand side), it's meaning to refresh ftp file list,
        # and back to original working path, ignore where ever the current working path.

            self.__is_login = True
            if change_to_backup == False:
                self.working_directory['remote'] = self.remote_pwd()[1]
                self.working_directory['remote_backup'] = self.working_directory['remote']
        else:
            msg = conn_msg

        # change to mountpoint
        if status == 0:
            if change_to_backup == False:
                status, msg = self.remote_change_current_folder(
                    cwd=self.mountpoint)
                if status == 0:
                    self.working_directory['remote'] = self.remote_pwd()[1]
                    self.working_directory['remote_backup'] = self.working_directory['remote']
            else:
                status, msg = self.remote_change_current_folder(
                    cwd=self.working_directory['remote_backup'])
                self.working_directory['remote'] = self.remote_pwd()[1]
        if status == 0:
            welcome = conn_msg
        return status, msg, welcome

    def remote_close(self):
        self.close()
        return 0

    def remote_modify_timestamp(self, dst_path):
        locale.setlocale(locale.LC_TIME, "en_US.UTF-8")
        now = datetime.now()
        str_timestamp = '{}'.format(now.strftime('%Y%m%d%H%M%S'))
        str_cmd = 'MFMT %s %s'%(str_timestamp, dst_path)
        print(str_cmd)
        self.remote_command(str_cmd)

    def reset_timeout_count(self, status=0):
        self.access_timestamp = time()
        self.access_status = status

    def Is_timeout_reconnect(self):
        if self.access_status == -1:
            return True

        if (time() - self.access_timestamp) > self.access_diff:
            return True
        else:
            return False

    def remote_quit(self):
        if self.__is_login == False:
            return
        try:
            self.quit()
            self.access_status = -1
        except (Exception, socket.error) as err:
            if __name__ != '__main__':
                orisolLog(level='error', module_name=Module_Name,
                          message='remote_quit err:%s' % (err))
        finally:
            pass

    def remote_noop(self):
        status = -1
        try:
            self.voidcmd('NOOP')
            status = 0
            self.reset_timeout_count()
        except (Exception, socket.error) as err:
            if __name__ != '__main__':
                orisolLog(level='error', module_name=Module_Name,
                          message='remote_noop err:%s' % (err))
            else:
                print(err)
        finally:
            return status

    @synchronized
    def remote_welcome(self):
        status = -1
        msg = ''
        try:
            msg = self.getwelcome()
            status = 0
        except (error_perm, Exception, socket.error) as err:
            if __name__ != '__main__':
                orisolLog(level='error', module_name=Module_Name,
                          message='remote_welcome err:%s' % str(err))
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
                self.reset_timeout_count()
                msg = self.pwd()
                status = 0
            except (error_perm, Exception, socket.error) as err:
                if __name__ != '__main__':
                    orisolLog(
                        level='error',
                        module_name=Module_Name,
                        message='pwd err:%s' % (str(err)))
                else:
                    print(err)
                msg = str(err)
            finally:
                pass
        return status, msg

    # def remote_get_ls(self, search_path=None):
    #     # return remote-list includes files and dirs
    #     # and cannot find the different
    #     status = -1
    #     remote_list = []
    #     try:
    #         remote_list = self.nlst(search_path)
    #         status = 0
    #     except Exception as e:
    #         if __name__ != '__main__':
    #             orisolLog(level='error', module_name=Module_Name,
    #                       message='remote_get_ls err:%s' % (e))
    #         else:
    #             print(e)
    #     finally:
    #         return status, remote_list

    def line_append(self, line):
        ignore_msg = ['*cmd*', '*resp*']
        if bool([ele for ele in ignore_msg if (ele in line)]):
            return
        self.ls_list.append(line)

    @synchronized
    def remote_get_file_list(self, search_path=None):
        status = -1
        pwd_status = 0
        self.set_debuglevel(1)
        remote_list = []
        err_msg = ''
        ignore_msg = ['*cmd*', '*resp*']
        if search_path != None and search_path != '' and search_path[0] != '/':
            search_path = '/' + search_path[:]

        if search_path == None or search_path == '':
            cmd = 'LIST'
            pwd_status, pwd = self.remote_pwd()
            if pwd_status == 0:
                search_path = pwd
        else:
            cmd = 'LIST %s' % (search_path)

        if __name__ != '__main__':
            orisolLog(level='debug', module_name=Module_Name,
                      message='get file list:%s' % (cmd))
        try:
            # -debug
            lines = ''
            self.ls_list.clear()
            self.retrlines(cmd, callback=self.line_append)
            status = 0
        except (error_perm, Exception, socket.error) as err:
            err_msg = str(err)
            if __name__ != '__main__':
                orisolLog(level='error', module_name=Module_Name,
                          message='remote_get_file_list:%s' % (str(err)),)
            else:
                print('remote-list err:', err)

        finally:
            self.set_debuglevel(self.debug_level)
            if status == 0 and pwd_status == 0:
                self.reset_timeout_count()
                for line in self.ls_list:
                    if bool([ele for ele in ignore_msg if (ele in line)]):
                        continue

                    file_attribute = {
                        'file_permissions': '',
                        'file_name': '',
                        'file_path': '',
                        'file_type': '',
                        'file_size': '',
                        'size_human': '',
                        'date': '',
                    }
                    file_permissions = line[0:10]
                    file_tmp = ' '.join(line[11:].split()).split(' ')
                    if file_permissions[0] == 'd':
                        file_attribute['file_type'] = 'DIR'
                        file_attribute['file_name'] = self.filename_parsing(
                            file_tmp, 7)
                        file_attribute['file_path'] = os.path.join(
                            search_path, file_attribute['file_name']
                        )
                    elif file_permissions[0] == 'l':
                        file_attribute['file_type'] = 'SYMLINK'
                        file_attribute['file_name'] = self.filename_parsing(
                            file_tmp, 7, attr='l')
                        file_attribute['file_path'] = os.path.join(
                            search_path, file_attribute['file_name']
                        )
                    else:
                        file_attribute['file_type'] = (
                            os.path.splitext(file_tmp[7])[1]
                        )[1:].upper()
                        file_attribute['file_name'] = self.filename_parsing(
                            file_tmp, 7)
                        file_attribute['file_path'] = os.path.join(
                            search_path, file_attribute['file_name']
                        )
                    file_attribute['file_permissions'] = file_permissions
                    file_attribute['file_size'] = int(file_tmp[3])
                    file_attribute['size_human'] = pretty_size(
                        int(file_tmp[3]))
                    str_date = ''
                    if ':' in file_tmp[6]:
                        str_date += '----'
                    else:
                        str_date += file_tmp[6]
                    str_date += '/' + file_tmp[4]
                    str_date += '/' + file_tmp[5]
                    if ':' in file_tmp[6]:
                        str_date += '/' + file_tmp[6]
                    file_attribute['date'] = str_date

                    # status, str_date = self.remote_get_mdtm(file_attribute['file_path'])
                    # if status == 0:
                    #     file_attribute['date'] = str_date
                    remote_list.append(file_attribute)
                    sorted(remote_list, key=lambda x: ['file_name'])
                return status, remote_list
            else:
                return status, err_msg

    @synchronized
    def remote_get_mdtm(self, search_path=None):
        str_mdtm = ''
        err_msg = ''
        if search_path == None:
            return -1
        self.set_debuglevel(1)
        cmd = 'mdtm %s' % (search_path)
        # self.sendcmd(cmd)
        status = -1

        try:
            with Capturing() as lines:
                self.sendcmd(cmd)

        except (Exception, socket.error) as err:
            if __name__ != '__main__':
                orisolLog(
                    level='error',
                    module_name=Module_Name,
                    message='remote-mdtm:%s-%s' % (search_path, err),
                )
            else:
                print('remote-mdtm err: %s, %s' % (search_path, err))

        finally:
            self.set_debuglevel(self.debug_level)
            for line in lines:
                if 'resp' in line and '213' in line:
                    bad_chars = '*resp'
                    str_c = ''.join(c for c in line if c not in bad_chars)
                    str_c = str_c.replace("'", '')
                    str_tokens = str_c.split()
                    str_mdtm = str_tokens[1]
                    status = 0
        return status, str_mdtm

    @synchronized
    def remote_mkdir(self, path_name):
        status = -1
        msg = ''
        try:
            msg = self.mkd(path_name)
            status = 0
            self.reset_timeout_count()
        except (error_perm, Exception, socket.error) as err:
            if __name__ != '__main__':
                orisolLog(level='info', module_name=Module_Name,
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
            msg = self.rmd(path_name)
            status = 0
            self.reset_timeout_count()
        except (error_perm, Exception, socket.error) as err:
            if __name__ != '__main__':
                orisolLog(level='info', module_name=Module_Name,
                          message='rmdir err:%s' % (str(err)))
            else:
                print(err)
            msg = str(err)
        finally:
            pass
        return status, msg

    @synchronized
    def remote_rmfile(self, file_name):
        status = -1
        msg = ''
        try:
            msg = self.delete(file_name)
            status = 0
            self.reset_timeout_count()
        except (error_perm, Exception, socket.error) as err:
            if __name__ != '__main__':
                orisolLog(level='info', module_name=Module_Name,
                          message='rmfile err:%s' % (str(err)))
            else:
                print(err)
            msg = str(err)
        finally:
            pass
        return status, msg

    @synchronized
    def remote_rename(self, old_name, new_name):
        status = -1
        msg = ''
        try:
            msg = self.rename(old_name, new_name)
            status = 0
            self.reset_timeout_count()
        except (error_perm, Exception, socket.error) as err:
            if __name__ != '__main__':
                orisolLog(level='info', module_name=Module_Name,
                          message='rename err:%s' % (str(err)))
            else:
                print(err)
            msg = str(err)
        finally:
            pass
        return status, msg

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
        if cwd == None:
            return -1, 'input argument failed'

        if cwd == '..':
            dir_token = os.path.split(self.working_directory['remote_backup'])
            tmp_cwd = dir_token[0]
        elif cwd == '/':
            tmp_cwd = self.mountpoint
        elif cwd != None:
            tmp_cwd = os.path.join(self.working_directory['remote_backup'], cwd)

        try:
            msg = self.cwd(tmp_cwd)
            status = 0
            self.reset_timeout_count()

        except (error_perm, socket.error) as err:
            if __name__ != '__main__':
                orisolLog(level='info', module_name=Module_Name,
                          message='cwd err:%s' % (str(err)))
            else:
                print(err)
            msg = str(err)
        finally:
            if status == 0:
                self.working_directory['remote'] = self.remote_pwd()[1]
                self.working_directory['remote_backup'] = tmp_cwd
        return status, msg

    @synchronized
    def remote_change_up_current_folder(self):
        # Change working directory to the parent of the current directory
        status = -1
        msg = ''
        try:
            str_resp = self.sendcmd('CDUP')
            if '250' in str_resp:
                status = 0
                msg = str_resp
                self.reset_timeout_count()
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

    def local_get_current_folder(self):
        return self.local_current_directory

    def search_local_file(self, search_path=None, ext_filter_list=None):
        filelist = []
        for root, dirs, files in os.walk(top=search_path, topdown=False):
            for file in files:
                if root == search_path:
                    if ext_filter_list == None:
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
        if search_path == None:
            search_path = self.local_current_directory

        if os.path.exists(search_path) == True:
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
        if os.path.exists(search_path) == True:
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
                new_path = os.path.join(
                    self.local_current_directory, folder_name)
            else:
                new_path = os.path.join(path_1, path_2)

        try:
            if os.path.exists(new_path) == True:
                self.local_current_directory = new_path
                status = 0
            else:
                print('set_local_current_folder not exist')

        except (Exception, socket.error) as err:
            print('set_local_current_folder err:', err)

        finally:
            print(
                'local new path:%s'
                % (self.local_get_current_folder()))
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
    def set_bulktag_info(self, dst_filenamelist=None, dst_mount=None,
                         src_mount=None, src_filenamelist=None,):
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
        while (self.bulktag.get_retry_count() > 0 and mass_transmmit_status == -1):
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

                status, msg = self.remote_download(src_filename=self.bulktag.get_src_filename_by_index(),
                                                   src_mount=self.bulktag.get_src_mount(),
                                                   dst_mount=self.bulktag.get_dst_mount())
                if status == 0:
                    idx += 1
                    self.bulktag.set_index(index=idx)
                else:
                    if msg[0] in self.negative_resp:
                        mass_transmmit_stop = 1
                        err_msg = msg
                    break

            if mass_transmmit_stop == 1:
                break

        escape_time = time() - start_time
        if mass_transmmit_status == 0:
            status, msg, welcome = self.remote_open(change_to_backup=True)
        return mass_transmmit_status, escape_time, err_msg

    @synchronized
    def bulk_upload(self):
        mass_transmmit_status = -1
        mass_transmmit_stop = 0
        err_msg = ''

        start_time = time()
        while (self.bulktag.get_retry_count() > 0 and mass_transmmit_status == -1):
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
                status, msg = self.remote_upload(src_filename=self.bulktag.get_src_filename_by_index(),
                                                 src_mount=self.bulktag.get_src_mount(),
                                                 dst_mount=self.bulktag.get_dst_mount())
                if status == 0:
                    idx += 1
                    self.bulktag.set_index(index=idx)
                else:
                    if msg[0] in self.negative_resp:
                        mass_transmmit_stop = 1
                        err_msg = msg
                    break

            if mass_transmmit_stop == 1:
                break

        escape_time = time() - start_time
        if mass_transmmit_status == 0:
            status, msg, welcome = self.remote_open(change_to_backup=True)
        return mass_transmmit_status, escape_time, err_msg

    @synchronized
    def bulk_delete(self):
        mass_transmmit_status = -1
        mass_transmmit_stop = 0
        err_msg = ''

        start_time = time()
        while (self.bulktag.get_retry_count() > 0 and mass_transmmit_status == -1):
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
                    self.bulktag.get_dst_mount(), self.bulktag.get_dst_filename_by_index())
                status, msg = self.remote_rmfile(dst_filepath)
                if status == 0:
                    idx += 1
                    self.bulktag.set_index(index=idx)
                else:
                    if msg[0] in self.negative_resp:
                        mass_transmmit_stop = 1
                        err_msg = msg
                    break

            if mass_transmmit_stop == 1:
                break

        escape_time = time() - start_time
        if mass_transmmit_status == 0:
            status, msg, welcome = self.remote_open(change_to_backup=True)
        return mass_transmmit_status, escape_time, err_msg

    @synchronized
    def bulk_rename(self):
        mass_transmmit_status = -1
        mass_transmmit_stop = 0
        err_msg = ''
        start_time = time()
        while (self.bulktag.get_retry_count() > 0 and mass_transmmit_status == -1):
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
                status, msg = self.remote_rename(old_name=self.bulktag.get_src_filename_by_index(),
                                                 new_name=self.bulktag.get_dst_filename_by_index())
                if status == 0:
                    idx += 1
                    self.bulktag.set_index(index=idx)
                else:
                    if msg[0] in self.negative_resp:
                        mass_transmmit_stop = 1
                        err_msg = msg
                    break

            if mass_transmmit_stop == 1:
                break

        escape_time = time() - start_time
        if mass_transmmit_status == 0:
            status, msg, welcome = self.remote_open(change_to_backup=True)
        return mass_transmmit_status, escape_time, err_msg

    @synchronized
    def remote_upload(self, src_filename, dst_filename=None,
                      src_mount=None, dst_mount=None):
        status = -1
        msg = 0
        bufsize = 1024
        local_filepath = ''
        remote_filepath = ''
        remote_dir = ''
        if src_mount == None:
            local_filepath = os.path.join(
                self.local_current_directory, src_filename)
        else:
            local_filepath = os.path.join(src_mount, src_filename)
        if not os.path.isfile(local_filepath):
            return -2, 'cannot find local file'
        if dst_mount != None and dst_mount[0] != '/':
            dst_mount = '/'+dst_mount[:]
        if dst_mount == None and dst_filename == None:
            remote_filepath = os.path.join(
                self.working_directory['remote_backup'], src_filename)
            remote_dir = self.working_directory['remote_backup']
        elif dst_mount == None and dst_filename != None:
            remote_filepath = os.path.join(
                self.working_directory['remote_backup'], dst_filename)
            remote_dir = self.working_directory['remote_backup']
        elif dst_mount != None and dst_filename == None:
            remote_filepath = os.path.join(dst_mount, src_filename)
            remote_dir = dst_mount
        elif dst_mount != None and dst_filename != None:
            remote_filepath = os.path.join(dst_mount, dst_filename)
            remote_dir = dst_mount
        local_f = open(local_filepath, 'rb')
        try:
            cmd = 'STOR %s' % (remote_filepath)
            msg = self.storbinary(cmd, fp=local_f, blocksize=bufsize)
            status = 0
        except (error_perm, Exception, socket.error) as err:
            if __name__ != '__main__':
                orisolLog(level='info', module_name=Module_Name,
                          message='remote_upload err:%s' % (str(err)))
            else:
                print('remote_upload err:', err)
            msg = str(err)
        finally:
            local_f.close()
            return status, msg

    @synchronized
    def remote_download(self, src_filename, dst_filename=None,
                        src_mount=None, dst_mount=None):
        status = -1
        msg = ''
        bufsize = 1024
        local_filepath = ''
        remote_filepath = ''
        if src_mount == None:
            remote_filepath = os.path.join(
                self.working_directory['remote_backup'], src_filename)
        else:
            remote_filepath = os.path.join(src_mount, src_filename)
        if dst_mount != None and dst_mount[0] != '/':
            dst_mount = '/'+dst_mount[:]
        if dst_mount == None and dst_filename == None:
            local_filepath = os.path.join(
                self.local_current_directory, src_filename)
        elif dst_mount == None and dst_filename != None:
            local_filepath = os.path.join(
                self.local_current_directory, dst_filename)
        elif dst_mount != None and dst_filename == None:
            local_filepath = os.path.join(dst_mount, src_filename)
        elif dst_mount != None and dst_filename != None:
            local_filepath = os.path.join(dst_mount, dst_filename)
        if not os.path.isdir(local_filepath) and os.path.exists(local_filepath):
            os.remove(local_filepath)
        local_f = open(local_filepath, 'wb')
        try:
            cmd = 'RETR %s' % (remote_filepath)
            msg = self.retrbinary(cmd, local_f.write, blocksize=bufsize)
            status = 0
        except (error_perm, Exception, socket.error) as err:
            status = -1
            if __name__ != '__main__':
                orisolLog(level='info', module_name=Module_Name,
                          message='remote_download err:%s' % (str(err)))
            else:
                print(err)
            msg = str(err)
        finally:
            local_f.close()
            if status == -1:
                if not os.path.isdir(local_filepath) and os.path.exists(local_filepath):
                    os.remove(local_filepath)
            return status, msg

    def recursively_upload(self, src, dst):
        status = 0
        mass_transmmit_status = -1
        mass_transmmit_stop = 0
        err_msg = ''
        self.set_bulktag_info()

        if src[0] != '/':
            src = '/'+src[:]
        if dst[0] != '/':
            dst = '/'+dst[:]

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
                    if dst_item['file_type'] == 'DIR' and dst_item['file_path'] == sub_dst:
                        sub_dst_exist = True
                        break
                if sub_dst_exist == False:
                    status = self.remote_mkdir(sub_dst)
                self.recursively_upload(sub_src, sub_dst)
            else:

                sub_src_tokens = os.path.split(sub_src)
                sub_dst_tokens = os.path.split(sub_dst)

                while (self.bulktag.get_retry_count() > 0 and mass_transmmit_stop == 0):
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
            status, msg, welcome = self.remote_open(change_to_backup=True)
        return mass_transmmit_status, escape_time, err_msg

    def recursively_download(self, src, dst):
        status = 0
        mass_transmmit_status = -1
        mass_transmmit_stop = 0
        err_msg = ''
        self.set_bulktag_info()

        if src[0] != '/':
            src = '/'+src[:]
        if dst[0] != '/':
            dst = '/'+dst[:]

        start_time = time()
        status, msg, welcome = self.remote_open()
        if status != 0:
            err_msg = msg
            escape_time = time() - start_time
            return mass_transmmit_status, escape_time, err_msg

        status, src_items = self.remote_get_file_list(src)
        for src_item in src_items:
            sub_src = src_item['file_path']
            sub_dst = os.path.join(
                dst, os.path.split(src_item['file_path'])[1])
            if src_item['file_type'] == 'DIR':
                if not os.path.isdir(sub_dst):
                    os.mkdir(sub_dst)
                self.recursively_download(sub_src, sub_dst)
            else:
                if os.path.exists(sub_dst):
                    os.remove(sub_dst)

                sub_src_tokens = os.path.split(sub_src)
                sub_dst_tokens = os.path.split(sub_dst)

                while (self.bulktag.get_retry_count() > 0 and mass_transmmit_stop == 0):
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
            status, msg, welcome = self.remote_open(change_to_backup=True)
        return mass_transmmit_status, escape_time, err_msg

    def recursively_remove(self, dst):
        status = 0
        mass_transmmit_status = -1
        mass_transmmit_stop = 0
        err_msg = ''
        self.set_bulktag_info()
        if dst[0] != '/':
            dst = os.path.join(self.working_directory['remote_backup'],dst)
        start_time = time()
        status, msg, welcome = self.remote_open(change_to_backup=True)
        if status != 0:
            err_msg = msg
            escape_time = time() - start_time
            return mass_transmmit_status, escape_time, err_msg

        status, dst_items = self.remote_get_file_list(dst)
        for dst_item in dst_items:
            sub_dst = dst_item['file_path']
            if dst_item['file_type'] == 'DIR':
                self.recursively_remove(sub_dst)
                self.remote_rmdir(sub_dst)
            else:
                while (self.bulktag.get_retry_count() > 0 and mass_transmmit_stop == 0):
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
            status, msg, welcome = self.remote_open(change_to_backup=True)
        return mass_transmmit_status, escape_time, err_msg

    def local_recursively_copy(self, src, dst):
        status = True
        for iter in os.listdir(src):
            sub_src = os.path.join(src, iter)
            sub_dst = os.path.join(dst, iter)
            if os.path.isdir(sub_src):
                if not os.path.isdir(sub_dst):
                    os.mkdir(sub_dst)
                self.local_recursively_copy(sub_src, sub_dst)
            else:
                if os.path.exists(sub_dst):
                    os.remove(sub_dst)
                shutil.copy2(sub_src, sub_dst)
        return status

    def local_recursively_remove(self, dst):
        status = True
        for iter in os.listdir(dst):
            sub_dst = os.path.join(dst, iter)
            if os.path.isdir(sub_dst):
                self.local_recursively_remove(sub_dst)
                os.rmdir(sub_dst)
            else:
                if os.path.exists(sub_dst):
                    os.remove(sub_dst)
        return status

    def remote_command(self, str_command):
        str_resp = self.sendcmd(str_command)
        self.reset_timeout_count()
        return str_resp

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

        if src_mountpoint == None:
            remote_mountpoint = self.mountpoint
        else:
            remote_mountpoint = src_mountpoint

        if dst_mountpoint == None:
            local_mountpoint = self.local_tmp_directory
        else:
            local_mountpoint = dst_mountpoint

        for src_filepath in src_filepaths:
            remote_filepath = os.path.join(remote_mountpoint, src_filepath)
            status, info = self.remote_get_file_list(remote_filepath)
            if status == 0:
                self.gen_relative_local_directory(
                    local_mountpoint, src_filepath)
                local_filepath = os.path.join(local_mountpoint, src_filepath)
                if not os.path.isdir(local_filepath) and os.path.exists(local_filepath):
                    os.remove(local_filepath)

                local_f = open(local_filepath, 'wb')
                try:
                    cmd = 'RETR %s' % (remote_filepath)
                    msg = self.retrbinary(
                        cmd, local_f.write, blocksize=bufsize)
                    status = 0
                except (error_perm, Exception, socket.error) as err:
                    status = -1
                    if __name__ != '__main__':
                        orisolLog(level='info', module_name=Module_Name,
                                  message='remote_relative_download err:%s' % (str(err)))
                    else:
                        print(err)
                    msg = str(err)
                finally:
                    local_f.close()
                    if status == -1:
                        if not os.path.isdir(local_filepath) and os.path.exists(local_filepath):
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
                        orisolLog(level='info', module_name=Module_Name,
                                  message='remote_gen_relative_directory err:%s' % (str(msg)))
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

        if src_mountpoint == None:
            local_mountpoint = self.local_tmp_directory
        else:
            local_mountpoint = src_mountpoint

        if dst_mountpoint == None:
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
                cmd = 'STOR %s' % (remote_filepath)
                msg = self.storbinary(cmd, fp=local_f, blocksize=bufsize)
                status = 0
            except (error_perm, Exception, socket.error) as err:
                if __name__ != '__main__':
                    orisolLog(level='info', module_name=Module_Name,
                              message='remote_relative_upload err:%s' % (str(err)))
                else:
                    print('remote_relative_upload err:', err)
                msg = str(err)
            finally:
                local_f.close()
        return status, msg


def cli_help():
    help_doc = ''
    help_doc += '=====   command line help list   ====='+os.linesep
    help_doc += ' > "open"           : open and login ftp server'+os.linesep
    help_doc += ' > "ls"             : lists directory contents of files and directories'+os.linesep
    help_doc += ' > "cd"             : change current directory'+os.linesep
    help_doc += ' > "pwd"            : print current working directory'+os.linesep
    help_doc += ' > "mkdir"          : create a new directory'+os.linesep
    help_doc += ' > "rmdir"          : remove a directory'+os.linesep
    help_doc += ' > "rm"             : remove a file'+os.linesep
    help_doc += ' > "cdup"           : change wroking direcotry to parent directory'+os.linesep
    help_doc += ' > "put"            : upload file from local to remote'+os.linesep
    help_doc += ' > "get"            : download file from remote to local'+os.linesep
    help_doc += ' > "recur-upload"   : upload file and directory from local to remote recursively'+os.linesep
    help_doc += ' > "recur-download" : download file and directory from remote to local recursively'+os.linesep
    help_doc += ' > "recur-remove"   : delete file and directory from remote recursively'+os.linesep
    help_doc += '=====   end of command line help list   ====='
    print(help_doc)


class FtpProbe(threading.Thread):
    def __init__(self):
        super(FtpProbe, self).__init__()

    def __new__(cls, *args, **kw):
        if not hasattr(cls, '_instance'):
            cls._instance = super(FtpProbe,
                                  cls).__new__(cls, *args, **kw)
        return cls._instance

    def initParam(self):
        self.interval = 1
        self.is_enable = False
        self.cond = threading.Condition()
        self.ftpclient = FtpClient()
        self.stop_event = threading.Event()

    def quit(self):
        self.cond.acquire()
        self.cond.notify()
        self.cond.release()
        self.stop_event.set()

    def enable(self, command):
        if isinstance(command, bool):
            self.is_enable = command
            self.interval = 1
            if self.is_enable == True:
                self.cond.acquire()
                self.cond.notify()
                self.cond.release()

    def run(self):
        print('probe-run')
        while True:
            if self.is_enable == False:
                self.cond.acquire()
                self.cond.wait()
                self.cond.release()

            noop_status = self.ftpclient.remote_noop()
            if noop_status == 0:
                self.interval = 10
            print('FtpProbe-noop:', time())
            self.stop_event.wait(self.interval)
            if self.stop_event.is_set():
                break


class FtpConnDetect(threading.Thread):
    def __init__(self):
        super(FtpConnDetect, self).__init__()
        self.is_conn = False
        self.is_pause = False
        self.ftpclient = FtpClient()
        self.quit_event = threading.Event()

    def __new__(cls, *args, **kw):
        if not hasattr(cls, '_instance'):
            cls._instance = super(FtpConnDetect,
                                  cls).__new__(cls, *args, **kw)
        return cls._instance

    def get_detect(self):
        print('FtpConnDetect-get_detect')
        return self.is_conn

    def set_detect(self):
        print('FtpConnDetect-enable_detect')
        self.is_conn = False
        self.is_pause = False
        g_conn_detection_event.set()

    def pause_detect(self):
        print('FtpConnDetect-pause_detect')
        self.is_pause = True

    def quit(self):
        self.quit_event.set()
        g_conn_detection_event.set()

    def run(self):
        print('FtpConnDetect-run')
        g_conn_detection_event.wait()
        g_conn_detection_event.clear()

        while True:
            if self.quit_event.is_set():
                break
            if self.is_conn == False:
                if self.is_pause == True:
                    g_conn_detection_event.wait()
                    g_conn_detection_event.clear()
                    continue
                status, msg, welcome = self.ftpclient.remote_open()
                if status == 0:
                    print('conn-detect>> msg:%s, welcome:%s' % (msg, welcome))
                    self.is_conn = True
                    g_conn_detection_event.wait()
                    g_conn_detection_event.clear()
                else:
                    sleep(5)


def cli():
    ftp_tool = FtpClient()

    # cygwin-mirror
    # ftp_tool.init_parameter(
    #    host='cygwin.mirror.rafal.ca',
    #    username='ftp',
    #    password='orisol@orisol.com',
    #    secure=False,
    #    implicit_TLS=False,
    # )

    # rebex-net - implicit_TLS
    # ftp_tool.init_parameter(
    #     host='test.rebex.net',
    #     username='ftp',
    #     password='ftp',
    #     secure=True,
    #     implicit_TLS=True,
    #     port=990
    # )

    # rebex-net - explicit_TLS
    # ftp_tool.init_parameter(
    #     host='test.rebex.net',
    #     username='ftp',
    #     password='ftp',
    #     secure=True,
    #     implicit_TLS=False,
    #     mountpoint='pub/example',
    #     port=21
    # )

    # hinet-upload
    # ftp_tool.init_parameter(
    #     host='ftp.speed.hinet.net',
    #     username='ftp',
    #     password='ftp',
    #     mountpoint = '/',
    #     secure=False,
    #     implicit_TLS=False,
    #     port=21,
    #     connect_timeout=10,
    # )

    # orisol-nas
    ftp_tool.init_parameter(
        host='10.92.2.253',
        username='Orisoltest',
        password='Orisol1234',
        secure=True,
        implicit_TLS=False,
        connect_timeout=6,
        mountpoint='/Test/',
    )

    # ftp_tool.init_parameter(
    #     host='172.17.5.100',
    #     username='pcagyyaons',
    #     password='123456Ab*',
    #     secure=True,
    #     implicit_TLS=False,
    #     connect_timeout=6,
    #     mountpoint='/',
    # )

    # ftp_probe = FtpProbe()
    # ftp_probe.initParam()
    # ftp_probe.start()

    ftp_conn_detect = FtpConnDetect()
    ftp_conn_detect.start()

    while True:
        try:
            cli_input = input('>> ')
            cli_input_list = cli_input.split(' ')

            if len(cli_input_list) > 0:
                if cli_input_list[0] == 'q':
                    # ftp_probe.quit()
                    ftp_conn_detect.quit()
                    ftp_tool.remote_quit()
                    break

                elif cli_input_list[0] == 'help':
                    cli_help()

                elif cli_input_list[0] == 'host':
                    if len(cli_input_list) > 1:
                        ftp_tool.remote_set_host(
                            cli_input_list[1])

                elif cli_input_list[0] == 'user':
                    if len(cli_input_list) > 1:
                        ftp_tool.remote_set_username(
                            cli_input_list[1])

                elif cli_input_list[0] == 'pass':
                    if len(cli_input_list) > 1:
                        ftp_tool.remote_set_password(
                            cli_input_list[1])

                elif cli_input_list[0] == 'port':
                    if len(cli_input_list) > 1:
                        ftp_tool.remote_set_port(
                            cli_input_list[1])

                elif cli_input_list[0] == 'mount':
                    if len(cli_input_list) > 1:
                        ftp_tool.remote_set_mountpoint(
                            cli_input_list[1])

                elif cli_input_list[0] == 'show-para':
                    ftp_tool.remote_print_parameter()

                elif cli_input_list[0] == 'conn':
                    status, msg = ftp_tool.remote_connect()
                    log = 'status:%d, msg:%s' % (status, msg)
                    print(log)

                elif cli_input_list[0] == 'login':
                    status, msg = ftp_tool.remote_login()
                    log = 'status:%d, msg:%s' % (status, msg)
                    print(log)

                elif cli_input_list[0] == 'open':
                    status, msg, welcome = ftp_tool.remote_open()
                    log = 'status:%d, msg:%s, welcome:%s' % (
                        status, msg, welcome)
                    print(log)

                elif cli_input_list[0] == 'close':
                    ftp_tool.remote_close()

                elif cli_input_list[0] == 'quit':
                    ftp_tool.remote_quit()

                elif cli_input_list[0] == 'ls':
                    if ftp_tool.Is_timeout_reconnect():
                        ftp_tool.remote_open(change_to_backup=True)
                    if len(cli_input_list) > 1:
                        status, files = ftp_tool.remote_get_file_list(
                            cli_input_list[1])
                    else:
                        status, files = ftp_tool.remote_get_file_list()
                    if status == 0:
                        print('-----------------------------------------------------------------')
                        for file in files:
                            log = '%s  %s  %s  %s  %s  %s' % (file['file_permissions'],
                                                              file['file_name'].ljust(
                                20),
                                file['file_path'].ljust(
                                30),
                                file['file_type'].ljust(
                                10),
                                file['size_human'].ljust(
                                10),
                                file['date'].ljust(10))
                            print(log)
                        print('length of filelist:', len(files))

                elif cli_input_list[0] == 'mass-download':
                    if len(cli_input_list) == 3:
                        status, files = ftp_tool.remote_get_file_list(
                            cli_input_list[1])
                        if status == 0:
                            ftp_tool.set_bulktag_download_info_cli(
                                src_files=files, dst_mount=cli_input_list[2])
                            download_status, escape, msg = ftp_tool.bulk_download()
                            print('download_status:',
                                  download_status, escape, msg)
                    else:
                        log = 'cli cmd fault:%s' % (cli_input_list)
                        print(log)

                elif cli_input_list[0] == 'mass-upload':
                    if len(cli_input_list) == 3:
                        status, src_files = ftp_tool.local_get_file_list_absolute_path(
                            search_path=cli_input_list[1])
                        if status == 0:
                            ftp_tool.set_bulktag_upload_info_cli(
                                src_files=src_files, dst_mount=cli_input_list[2])
                            upload_status, escape, msg = ftp_tool.bulk_upload()
                            print('upload_status:', upload_status, escape, msg)

                    else:
                        log = 'cli cmd fault:%s' % (cli_input_list)
                        print(log)

                elif cli_input_list[0] == 'mdtm':
                    if len(cli_input_list) > 1:
                        status, file_date = ftp_tool.remote_get_mdtm(
                            cli_input_list[1])
                        print('status---', status)
                        print('file_date---', file_date)
                    else:
                        print('cli- wrong command, try again:', cli_input_list)

                elif cli_input_list[0] == 'noop':
                    status = ftp_tool.remote_noop()
                    # Though some servers ignore the NOOP command.
                    # Then you will have to send some commands that really do something, like PWD
                    # like hinet server.

                elif cli_input_list[0] == 'welcome':
                    status, str_welcome = ftp_tool.remote_welcome()
                    if status == 0:
                        log = '...%s...' % (str_welcome)
                        print(log)

                # elif cli_input_list[0] == 'probe-start':
                #     ftp_probe.enable(True)

                # elif cli_input_list[0] == 'probe-stop':
                #     ftp_probe.enable(False)

                elif cli_input_list[0] == 's':
                    ftp_conn_detect.set_detect()
                elif cli_input_list[0] == 'p':
                    ftp_conn_detect.pause_detect()

                elif cli_input_list[0] == 'g':
                    status = ftp_conn_detect.get_detect()
                    print('====status:', status)

                elif cli_input_list[0] == 'pwd':
                    if ftp_tool.Is_timeout_reconnect():
                        ftp_tool.remote_open(change_to_backup=True)
                    status, remote_pwd = ftp_tool.remote_pwd()
                    log = 'status=%d, remote_pwd:%s'%(status,remote_pwd)
                    print(log)
                elif cli_input_list[0] == 'mountinfo':
                    mountinfo = ftp_tool.get_remote_mountpoint_info()
                    print('%d, %s' % (status, str(mountinfo)))

                elif cli_input_list[0] == 'cd':
                    if len(cli_input_list) < 2:
                        print('cli- wrong command, try again:', cli_input_list)
                    else:
                        status, msg = ftp_tool.remote_change_current_folder(
                            cwd=cli_input_list[1])
                        log = 'cwd > status:%d, msg:%s' % (status, msg)
                        print(log)

                elif cli_input_list[0] == 'mkdir':
                    if len(cli_input_list) < 2:
                        print('cli- wrong command, try again:', cli_input_list)
                    else:
                        status = ftp_tool.remote_mkdir(cli_input_list[1])

                elif cli_input_list[0] == 'rmdir':
                    if len(cli_input_list) < 2:
                        print('cli- wrong command, try again:', cli_input_list)
                    else:
                        status = ftp_tool.remote_rmdir(cli_input_list[1])

                elif cli_input_list[0] == 'rm':
                    if len(cli_input_list) < 2:
                        print('cli- wrong command, try again:', cli_input_list)
                    else:
                        status = ftp_tool.remote_rmfile(cli_input_list[1])

                elif cli_input_list[0] == 'cdup':
                    status, msg = ftp_tool.remote_change_up_current_folder()
                    log = 'cdup status:%d, msg:%s' % (status, msg)
                    print(log)

                elif cli_input_list[0] == 'rm-all':
                    # cwd to target directory then rm-all
                    if len(cli_input_list) < 2:
                        status, files = ftp_tool.remote_get_file_list()
                        if status == 0:
                            filelist = []
                            for file in files:
                                if file['file_type'] == 'DIR':
                                    continue
                                else:
                                    filelist.append(file['file_path'])
                            for file in filelist:
                                status = ftp_tool.remote_rmfile(file)
                    else:
                        status, files = ftp_tool.remote_get_file_list(
                            cli_input_list[1])
                        if status == 0:
                            filelist = []
                            for file in files:
                                if file['file_type'] == 'DIR':
                                    continue
                                else:
                                    filelist.append(file['file_path'])
                            for file in filelist:
                                status = ftp_tool.remote_rmfile(file)

                elif cli_input_list[0] == 'mv':
                    if len(cli_input_list) < 3:
                        print('cli- wrong command, try again:', cli_input_list)
                    else:
                        status, msg = ftp_tool.remote_rename(
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
                                status = ftp_tool.remote_upload(
                                    cli_input_list[1])
                                print('status:', status)

                            # 1. put <abs_path/filename>
                            # -> copy local absolute path filename to remote current folder
                            # -> and the same filename in remote current folder
                            else:
                                local_mount, filename = os.path.split(
                                    cli_input_list[1])
                                status = ftp_tool.remote_upload(
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
                                    status = ftp_tool.remote_upload(
                                        src_filename=local_filename,
                                        dst_filename=remote_filename)
                                    print('condition(1), status:', status)
                                elif (
                                        local_mount == '' and remote_mount != '' and remote_filename == ''):
                                    status = ftp_tool.remote_upload(
                                        src_filename=local_filename,
                                        dst_mount=remote_mount)
                                    print('condition(2), status:', status)
                                elif (
                                    local_mount == ''
                                    and remote_mount != ''
                                    and remote_filename != ''
                                ):
                                    status = ftp_tool.remote_upload(
                                        src_filename=local_filename,
                                        dst_mount=remote_mount,
                                        dst_filename=remote_filename,
                                    )
                                    print('condition(3), status:', status)

                                elif (
                                    local_mount != ''
                                    and remote_mount == ''
                                    and remote_filename != ''
                                ):
                                    status = ftp_tool.remote_upload(
                                        src_filename=local_filename,
                                        src_mount=local_mount,
                                        dst_filename=remote_filename,
                                    )
                                    print('condition(4), status:', status)

                                elif (
                                    local_mount != ''
                                    and remote_mount != ''
                                    and remote_filename == ''
                                ):
                                    status = ftp_tool.remote_upload(
                                        src_filename=local_filename,
                                        src_mount=local_mount,
                                        dst_mount=remote_mount,
                                    )
                                    print('condition(5), status:', status)

                                elif (
                                    local_mount != ''
                                    and remote_mount != ''
                                    and remote_filename != ''
                                ):
                                    status = ftp_tool.remote_upload(
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
                                status = ftp_tool.remote_download(
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
                                cli_input_list[1]
                            )
                            if os.path.isdir(cli_input_list[2]):
                                local_mount = cli_input_list[2]
                                local_filename = ''
                            else:
                                local_mount, local_filename = os.path.split(
                                    cli_input_list[2]
                                )
                                if (
                                    local_mount != ''
                                    and os.path.isdir(local_mount) != True
                                ):
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
                                    status = ftp_tool.remote_download(
                                        src_filename=remote_filename,
                                        dst_filename=local_filename,
                                    )
                                    print('condition(1), status:', status)
                                elif (
                                    local_mount != ''
                                    and remote_mount == ''
                                    and local_filename == ''
                                ):
                                    status = ftp_tool.remote_download(
                                        src_filename=remote_filename,
                                        dst_mount=local_mount,
                                    )
                                    print('condition(2), status:', status)
                                elif (
                                    local_mount != ''
                                    and remote_mount == ''
                                    and local_filename != ''
                                ):
                                    status = ftp_tool.remote_download(
                                        src_filename=remote_filename,
                                        dst_mount=local_mount,
                                        dst_filename=local_filename,
                                    )
                                    print('condition(3), status:', status)

                                elif (
                                    local_mount == ''
                                    and remote_mount != ''
                                    and local_filename != ''
                                ):
                                    status = ftp_tool.remote_download(
                                        src_filename=remote_filename,
                                        src_mount=remote_mount,
                                        dst_filename=local_filename,
                                    )
                                    print('condition(4), status:', status)

                                elif (
                                    local_mount != ''
                                    and remote_mount != ''
                                    and local_filename == ''
                                ):
                                    status = ftp_tool.remote_download(
                                        src_filename=remote_filename,
                                        src_mount=remote_mount,
                                        dst_mount=local_mount,
                                        dst_filename=remote_filename,
                                    )
                                    print('condition(5), status:', status)

                                elif (
                                    local_mount != ''
                                    and remote_mount != ''
                                    and local_filename != ''
                                ):
                                    status = ftp_tool.remote_download(
                                        src_filename=remote_filename,
                                        src_mount=remote_mount,
                                        dst_mount=local_mount,
                                        dst_filename=local_filename,
                                    )
                                    print('condition(6), status:', status)
                elif cli_input_list[0] == 'gets':
                    # gets uploadtest/ori.pgs downloadtest/99.ppi
                    if len(cli_input_list) < 2:
                        print('cli- wrong command, try again:', cli_input_list)
                    else:
                        filepaths = []
                        for idx, item in enumerate(cli_input_list):
                            if idx > 0:
                                filepaths.append(item)
                        ftp_tool.remote_open(change_to_backup=True)
                        ftp_tool.remote_relative_download(filepaths)
                elif cli_input_list[0] == 'getx':
                    # gets uploadtest/ori.pgs downloadtest/99.ppi
                    filepaths = ['wade/uploadtest/ori.pgs',
                                 'wade/downloadtest/99.ppi']
                    ftp_tool.remote_open(change_to_backup=True)
                    ftp_tool.remote_relative_download(filepaths)
                elif cli_input_list[0] == 'putx':
                    # gets uploadtest/ori.pgs downloadtest/99.ppi
                    filepaths = ['wade/F01.01/filemanager.py',
                                 'wade/F02.02/thumbnail.py']
                    ftp_tool.remote_open(change_to_backup=True)
                    ftp_tool.remote_relative_upload(filepaths)

                elif cli_input_list[0] == 'cmd':
                    ftp_cmd = ''
                    for idx, item in enumerate(cli_input_list):
                        if idx > 0:
                            ftp_cmd += item
                            if idx+1 == len(cli_input_list):
                                break
                            else:
                                ftp_cmd += ' '
                    str_resp = ftp_tool.remote_command(ftp_cmd)
                    log = 'ftp_cmd:%s, resp:%s' % (ftp_cmd, str_resp)
                    print(log)

                elif cli_input_list[0] == 'recur-upload':
                    if len(cli_input_list) == 3:
                        ftp_tool.recursively_upload(
                            cli_input_list[1], cli_input_list[2])
                elif cli_input_list[0] == 'recur-download':
                    if len(cli_input_list) == 3:
                        ftp_tool.recursively_download(
                            cli_input_list[1], cli_input_list[2])
                elif cli_input_list[0] == 'recur-remove':
                    if len(cli_input_list) == 2:
                        ftp_tool.recursively_remove(cli_input_list[1])
                # ---local --------------------------------------------------------------
                elif cli_input_list[0] == 'lpwd':
                    local_pwd = ftp_tool.local_get_current_folder()
                    print('local current folder:%s' % (local_pwd))
                elif cli_input_list[0] == 'lls':
                    if len(cli_input_list) > 1:
                        status, local_filelist = ftp_tool.local_get_file_list(
                            search_path=cli_input_list[1])
                    else:
                        status, local_filelist = ftp_tool.local_get_file_list()
                    if status == 0:
                        print(local_filelist)
                elif cli_input_list[0] == 'lcd':
                    if len(cli_input_list) < 2:
                        print('cli- wrong command, try again:', cli_input_list)
                    else:
                        status = ftp_tool.local_change_currnet_folder(
                            cli_input_list[1])
                else:
                    print('cli- wrong command, try again')
            else:
                pass
        except KeyboardInterrupt:
            print('key interrupt occur !!!')
            break


def main():
    cli()
    sys.exit(0)


if __name__ == '__main__':
    main()
