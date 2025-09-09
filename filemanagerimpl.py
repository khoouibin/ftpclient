import time
import sys
import traceback
import json
import os
import glob
import pathlib
import shutil
import subprocess
import threading
from pathlib import Path
import datetime
from termcolor import colored
import copy

# from uhf_host_common.interface.pdc_client import send_pdc_event, \
#     PdcEEvent, \
#     PdcDummyParam
try:
    from uhf_host_common.interface.logger_client import orisolLog
except ImportError:
    from uhf_host_file_manager.interface.logger_client import orisolLog

from uhf_host_file_manager.interface.ui_interface import send_msg_to_ui
from uhf_host_file_manager.services.thumbnail import ThumbnailGenerator
from uhf_host_file_manager.services.settings import Settings
from uhf_host_file_manager.services.parser import PgsParser, \
    JobParser, \
    PpiParser
from uhf_host_file_manager.services.smbclient import SmbClient
from uhf_host_file_manager.services.ftpclient import FtpClient
from uhf_host_file_manager.services.sftpclient import SSH_Ftp_Client
from uhf_host_common.return_code import get_return_code_by_error_definition


Service_Name = 'FilemanagerImpl'


settings = Settings()
jobs_folder_path = settings.getFilePathConf('JOBS_FOLDER')
prog_folder_path = settings.getFilePathConf('PROG_FOLDER')
local_root_folder_path = settings.getFilePathConf('LOCAL_ROOT_FOLDER')
smb_folder_path = settings.getFilePathConf('SMB_FOLDER')
ftp_folder_path = settings.getFilePathConf('FTP_FOLDER')
observe_folder_path = settings.getFilePathConf('OBSERVE_FOLDER')
share_folder_config = settings.getShareFolderConf()
ftp_client_config = settings.getFtpClientConf()
ftp_probe_event = threading.Event()

def synchronized(func):
    func.__lock__ = threading.RLock()

    def lock_func(*args, **kwargs):
        with func.__lock__:
            return func(*args, **kwargs)

    return lock_func


def report_error_message_to_ui(message, error_code=-1):
    ui_message_pkg = {"cmd": "notification", "error_code": error_code,
                      "message": message, "status": "general_alarm_occur"}
    send_msg_to_ui(msg=json.dumps(ui_message_pkg))

def report_smb_message_to_ui(message, status, error_code=0):
    ui_message_pkg = {"cmd": "notification", "error_code": error_code,
                      "message": message, "status": status}
    send_msg_to_ui(msg=json.dumps(ui_message_pkg))
    #"status": "smb_service_disconnect"

def report_ftp_message_to_ui(message, error_code=0):
    ui_message_pkg = {"cmd": "notification", "error_code": error_code,
                      "message": message, "status": "ftp_probe_report"}
    send_msg_to_ui(msg=json.dumps(ui_message_pkg))

def sync_after_file_access(func):
    def warp(*args, **kwargs):
        reply = func(*args, **kwargs)
        res = os.system("sync")
        message = "sync cache to disk, res=%d" % (res)
        orisolLog(level='debug', module_name=Service_Name, message=message)
        return reply
    return warp


class FilemanagerImpl:
    def __init__(self):
        self.log = {}

        smb_server_ip = share_folder_config['Import_Network_IP_Address']
        smb_username = share_folder_config['Import_Network_Username']
        smb_password = share_folder_config['Import_Network_Password']
        smb_importfolder = share_folder_config['Import_Network_Shared_Directory']
        smb_exportfolder = share_folder_config['Export_Network_Shared_Directory']
        smb_folder = ''
        if smb_folder_path != None:
            if smb_folder_path != jobs_folder_path and smb_folder_path != prog_folder_path:
                smb_folder = os.path.join(os.getcwd(), smb_folder_path)
                msg = json.loads(self.isFileExist(smb_folder))
                if msg['status'] == get_return_code_by_error_definition("ORS_SUCCESS"):
                    self.remove(path=smb_folder)
                    self._create_directonary(file_path=smb_folder)
                else:
                    self._create_directonary(file_path=smb_folder)

        self.smbclient = SmbClient('smbclient')
        self.smbclient.init_parameter(
            smb_server_ip, smb_username, smb_password, smb_importfolder, smb_exportfolder, smb_folder)

        self.smbclient_probe = SmbProbe()
        self.smbclient_probe.init_parameter(
            smb_server_ip, smb_username, smb_password, smb_importfolder, smb_exportfolder, smb_folder)

        self.ftp_tmp_folder = ''
        if ftp_folder_path != None:
            if ftp_folder_path != jobs_folder_path and ftp_folder_path != prog_folder_path:
                self.ftp_tmp_folder = os.path.join(
                    os.getcwd(), ftp_folder_path)
                msg = json.loads(self.isFileExist(self.ftp_tmp_folder))
                if msg['status'] == get_return_code_by_error_definition("ORS_SUCCESS"):
                    self.remove(path=self.ftp_tmp_folder)
                    self._create_directonary(file_path=self.ftp_tmp_folder)
                else:
                    self._create_directonary(file_path=self.ftp_tmp_folder)
        b_Is_SFTP = int(ftp_client_config['Is_SFTP']) == 1
        if b_Is_SFTP:
            self.ftpclient = SSH_Ftp_Client()
        else:
            self.ftpclient = FtpClient()
        self.ftpclient.init_parameter(
            host=ftp_client_config['FTP_Server_IP'],
            username=ftp_client_config['FTP_Server_User_Name'],
            password=ftp_client_config['FTP_Server_User_Password'],
            local_current_directory=local_root_folder_path,
            local_tmp_directory=self.ftp_tmp_folder,
            port=int(ftp_client_config['FTP_Port']),
            implicit_TLS=int(ftp_client_config['Is_Implicit_TLS']) == 1,
            secure=int(ftp_client_config['Is_FTPS']) == 1,
            mountpoint=ftp_client_config['FTP_Mountpoint'],
            connect_timeout=int(ftp_client_config['FTP_Connect_Timeout']),
        )
        self.ftpserviceprobe = FtpServiceProbe()
        probe_config = copy.deepcopy(ftp_client_config)
        probe_config['local_root_folder_path'] = local_root_folder_path
        probe_config['ftp_tmp_folder'] = self.ftp_tmp_folder
        self.ftpserviceprobe.init_parameter(probeconfig=probe_config)
        self.ftpserviceprobe.start()

    def execute_command(self, command):
        stdoutput, erroutput = "", None
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True) as process:
            stdoutput, erroutput = process.communicate()
            stdoutput = stdoutput.decode("utf-8")
            erroutput = erroutput.decode("utf-8")
        # print(command, stdoutput, erroutput)
        return stdoutput, erroutput

    def probe(self):
        return json.dumps({'status': 0, 'message': ''})

    def get_pgs_bundle_list(self, file_path):
        pgs_bundle_list = []
        # file_path = str(file_path).replace(".PGS", ".pgs")
        # pgs_bundle_list.append(file_path.replace('.pgs', '.pgi'))
        # pgs_bundle_list.append(file_path.replace('.pgs', '.3d'))
        # pgs_bundle_list.append(file_path.replace('.pgs', '.ppi'))
        file_path_wo_extension = os.path.splitext(file_path)[0]
        pgs_bundle_list.append(file_path_wo_extension + ".pgi")
        pgs_bundle_list.append(file_path_wo_extension + ".3d")
        pgs_bundle_list.append(file_path_wo_extension + ".ppi")
        pgs_bundle_list.append(file_path_wo_extension + ".png")

        return pgs_bundle_list

    def get_pgs_thumbnail_list(self, file_path):
        pgs_thumbnail_list = []
        # file_path = str(file_path).replace(".PGS", ".pgs")
        # pgs_thumbnail_list.append(file_path.replace('.pgs', '_s.png'))
        # pgs_thumbnail_list.append(file_path.replace('.pgs', '_m.png'))
        # pgs_thumbnail_list.append(file_path.replace('.pgs', '.pgi'))
        file_path_wo_extension = os.path.splitext(file_path)[0]
        pgs_thumbnail_list.append(file_path_wo_extension + "_s.png")
        pgs_thumbnail_list.append(file_path_wo_extension + "_m.png")
        pgs_thumbnail_list.append(file_path_wo_extension + ".pgi")

        return pgs_thumbnail_list

    def get_job_bundle_list(self, file_path):
        job_bundle_list = []
        # file_path = str(file_path).replace(".JOB", ".job")
        # job_bundle_list.append(file_path.replace('.job', '.jbi'))
        file_path_wo_extension = os.path.splitext(file_path)[0]
        job_bundle_list.append(file_path_wo_extension + ".jbi")

        return job_bundle_list

    def _create_pgs_bundle(self, file_path):
        pgs_thumbnail_list = self.get_pgs_thumbnail_list(
            file_path=file_path)
        thumbnailgenerator = ThumbnailGenerator()

        thumbnailgenerator.process_task(
            src_file_path=file_path,
            dst_file_path=pgs_thumbnail_list[0],
            img_width=140, img_height=140)
        thumbnailgenerator.process_task(
            src_file_path=file_path,
            dst_file_path=pgs_thumbnail_list[1],
            img_width=340, img_height=340)
        thumbnailgenerator.process_task(
            src_file_path=file_path,
            dst_file_path=pgs_thumbnail_list[2],
            img_width=400, img_height=300)

    # def _write_job(self, file_path, json_content):
    #     job_parser = JobParser()
    #     job_parser.write(file_path=file_path, json_content=json_content)
    #     return json.dumps({'status': 0, 'message': 'write %s success' % file_path})

    # def _write_pgs(self, file_path, json_content):
    #     pgs_parser = PgsParser()
    #     pgs_parser.write(file_path=file_path, json_content=json_content)
    #     self._create_pgs_bundle(file_path=file_path)

        return json.dumps({'status': 0, 'message': 'write %s success' % file_path})

    def _write_file(self, file_path, file_type, json_content):
        if file_type == "JOB":
            job_parser = JobParser()
            job_parser.write(file_path=file_path, json_content=json_content)
            status = get_return_code_by_error_definition("ORS_SUCCESS")
            message = "write %s success" % (file_path)
        elif file_type == "PGS":
            pgs_parser = PgsParser()
            pgs_parser.write(file_path=file_path, json_content=json_content)
            self._create_pgs_bundle(file_path=file_path)
            status = get_return_code_by_error_definition("ORS_SUCCESS")
            message = "write %s success" % (file_path)
        elif file_type == "PPI":
            ppi_parser = PpiParser()
            status, message = ppi_parser.write(
                file_path=file_path, json_content=json_content)
        else:
            status = get_return_code_by_error_definition("ORS_SUCCESS")
            message = "write %s success" % (file_path)
            f = open(file_path, 'w')
            f.close()

        return json.dumps({'status': status, 'message': message})

    def _create_directonary(self, file_path):
        try:
            os.mkdir(file_path)
            if os.path.exists(file_path):
                return json.dumps({'status': 0, 'message': 'create dir %s success' % file_path})
            else:
                return json.dumps({'status': -1, 'message': 'create dir %s fail' % file_path})
        except OSError as e:
            print(e)

    @sync_after_file_access
    def createFile(self, file_path, file_type, json_content):
        str_tokens = str(file_path).split('/')
        file_name = str_tokens[-1]
        file_dir = str(file_path).replace(file_name, '')

        pathlib.Path(file_dir).mkdir(parents=True, exist_ok=True)

        log = '%s: content: %s' % (__name__, json_content)
        orisolLog(level='debug', module_name=Service_Name, message=log)

        if os.path.exists(file_path):
            return json.dumps({'status': get_return_code_by_error_definition("ERR_FAIL_TO_CREATE"), 'message': file_path + ' is already exist'})

        else:
            if file_type == "DIR":
                return self._create_directonary(file_path)
            else:
                return self._write_file(file_path, file_type, json_content)

    def isFileExist(self, file_path):
        if (os.path.exists(file_path)):
            return json.dumps({'status': get_return_code_by_error_definition("ORS_SUCCESS"), 'message': file_path + ' is exist'})
        else:
            return json.dumps({'status': get_return_code_by_error_definition("ERR_FAIL_TO_FIND"), 'message': file_path + ' is not exist'})

    @sync_after_file_access
    def writeFile(self, file_path, file_type, json_content, overwrite):
        file_dir = os.path.dirname(file_path)
        pathlib.Path(file_dir).mkdir(parents=True, exist_ok=True)

        if (os.path.exists(file_path) == True and overwrite == False):
            return json.dumps({'status': get_return_code_by_error_definition("ERR_FAIL_TO_WRITE"), 'message': file_path + ' is already exist'})

        else:
            return self._write_file(file_path, file_type, json_content)

    def readFile(self, file_path, file_type):
        if (os.path.exists(file_path) == False):
            return json.dumps({'status': get_return_code_by_error_definition("ERR_FAIL_TO_FIND"), 'message': file_path + ' is not exist'})
        if (os.path.isfile(file_path) == False):
            return json.dumps({'status': get_return_code_by_error_definition("ERR_INVALID_FILE_FORMAT"), 'message': file_path + ' is not file'})

        if (file_type == "PGS"):
            pgs_parser = PgsParser()
            json_pgs = pgs_parser.read(file_path)
            if json_pgs == None:
                # send_pdc_event(id=PdcEEvent.SewProgramError.value,
                #                param=str(PdcDummyParam))
                return json.dumps({'status': get_return_code_by_error_definition("ERR_FAIL_TO_READ"), 'message': "parse pgs file error"})
            elif json_pgs["PGS_Status"]["Is_Format_Correct"] == False:
                # send_pdc_event(id=PdcEEvent.SewProgramError.value,
                #                param=str(PdcDummyParam))
                return json.dumps({'status': get_return_code_by_error_definition("ERR_INVALID_FILE_FORMAT"), 'message': json_pgs})
            else:
                pgs_thumbnail_list = self.get_pgs_thumbnail_list(file_path=file_path)
                is_thumbnail_exist = True
                for pgs_thumbnail in pgs_thumbnail_list:
                    if os.path.exists(pgs_thumbnail) == False:
                        is_thumbnail_exist = False
                        break

                if is_thumbnail_exist == False:
                    self._create_pgs_bundle(file_path=file_path)

                return json.dumps({'status': get_return_code_by_error_definition("ORS_SUCCESS"), 'message': json_pgs})

        elif (file_type == "PGS_Header"):
            pgs_parser = PgsParser()
            json_pgs = pgs_parser.read(file_path, header_only=True)
            if json_pgs == None:
                return json.dumps({'status': get_return_code_by_error_definition("ERR_FAIL_TO_READ"), 'message': "parse pgs file error"})
            elif json_pgs["PGS_Status"]["Is_Format_Correct"] == False:
                return json.dumps({'status': get_return_code_by_error_definition("ERR_INVALID_FILE_FORMAT"), 'message': json_pgs})
            else:
                pgs_thumbnail_list = self.get_pgs_thumbnail_list(file_path=file_path)
                is_thumbnail_exist = True
                for pgs_thumbnail in pgs_thumbnail_list:
                    if os.path.exists(pgs_thumbnail) == False:
                        is_thumbnail_exist = False
                        break
                if is_thumbnail_exist == False:
                    self._create_pgs_bundle(file_path=file_path)
                return json.dumps({'status': get_return_code_by_error_definition("ORS_SUCCESS"), 'message': json_pgs})

        elif (file_type == "PPI"):
            ppi_parser = PpiParser()
            json_ppi = ppi_parser.read(file_path)
            if json_ppi == None:
                return json.dumps({'status': get_return_code_by_error_definition("ERR_FAIL_TO_READ"), 'message': "parse pgs file error"})
            else:
                return json.dumps({'status': get_return_code_by_error_definition("ORS_SUCCESS"), 'message': json_ppi})

        elif (file_type == "JOB"):
            job_parser = JobParser()
            json_job = job_parser.read(file_path=file_path)

            if json_job == None:
                status = get_return_code_by_error_definition("ERR_JOB_LOADDING")
                message = "load Job fail"
                report_error_message_to_ui(
                    message=message,
                    error_code=get_return_code_by_error_definition("POPUP_MESSAGE_JOB_LOADDING_ERROR"))
                # send_pdc_event(id=PdcEEvent.JobLoadingError.value,
                #                param=str(PdcDummyParam))
                return json.dumps({'status': status, 'message': message})
            else:
                status = get_return_code_by_error_definition("ORS_SUCCESS")
                return json.dumps({'status': status, 'message': json_job})

        else:
            f = open(file_path, 'r')
            reader = f.read()
            f.close()
            return json.dumps({'status': get_return_code_by_error_definition("ORS_SUCCESS"), 'message': str(reader)})

    @sync_after_file_access
    def updateFile(self, file_path, file_name, json_content):
        print('UpdateFile', file_path, file_name, json_content)
        return json.dumps({'status': 0})

    @sync_after_file_access
    def refineFileSys(self, path):
        parent_path = path
        if (os.path.isfile(path)):
            parent_path = os.path.dirname(path)

        reply = self.recursiveListFile(path=parent_path)
        json_reply = json.loads(reply)
        json_files = json_reply['list']

        for file in json_files:

            old_file_path = file['file_path']

            str_tokens = str(old_file_path).split('/')
            new_style = str_tokens[-3]
            new_size = str_tokens[-2]

            old_file_name = file['file_name']
            str_tokens = str(old_file_name).split('__')
            old_style = str_tokens[-3]
            old_size = str_tokens[-2]
            old_id_with_extension = str_tokens[-1]

            new_file_name = new_style + "__" + new_size + "__" + old_id_with_extension
            new_file_path = str(old_file_path).replace(
                old_file_name, new_file_name)

            reply = self.rename(old_file_path, new_file_path)

            json_reply = json.loads(reply)
            if json_reply['status'] != 0:
                return reply

        return json.dumps({'status': get_return_code_by_error_definition("ORS_SUCCESS"), "message": "refine file sys done"})

    @sync_after_file_access
    def rename(self, src_path, dst_path):
        if (os.path.exists(src_path) == False):
            return json.dumps({'status': get_return_code_by_error_definition("ERR_FAIL_TO_FIND"), "message": "source path %s not found" % (src_path)})
        elif (os.path.exists(dst_path) == True):
            return json.dumps({'status': get_return_code_by_error_definition("ERR_FAIL_TO_WRITE"), "message": "destination path %s is already existed" % (dst_path)})
        else:
            try:
                str_extension = os.path.splitext(dst_path)[-1]
                absolute_src_path = os.path.abspath(src_path)
                absolute_dst_path = os.path.abspath(dst_path)

                if str_extension == ".job":
                    src_job_bundle_list = self.get_job_bundle_list(src_path)
                    dst_job_bundle_list = self.get_job_bundle_list(dst_path)
                    bundle_n = len(src_job_bundle_list)
                    for index in range(0, bundle_n):
                        src_job_bundle_path = src_job_bundle_list[index]
                        dst_job_bundle_path = dst_job_bundle_list[index]
                        self.rename(src_job_bundle_path, dst_job_bundle_path)
                    shutil.move(src_path, dst_path)

                elif str_extension == ".pgs":
                    src_pgs_bundle_list = self.get_pgs_bundle_list(src_path)
                    dst_pgs_bundle_list = self.get_pgs_bundle_list(dst_path)
                    bundle_n = len(src_pgs_bundle_list)
                    for index in range(0, bundle_n):
                        src_pgs_bundle_path = src_pgs_bundle_list[index]
                        dst_pgs_bundle_path = dst_pgs_bundle_list[index]
                        self.rename(src_pgs_bundle_path, dst_pgs_bundle_path)

                    src_pgs_thumbnail_list = self.get_pgs_thumbnail_list(
                        src_path)
                    dst_pgs_thumbnail_list = self.get_pgs_thumbnail_list(
                        dst_path)
                    bundle_n = len(src_pgs_thumbnail_list)
                    for index in range(0, bundle_n):
                        src_pgs_thumbnail_path = src_pgs_thumbnail_list[index]
                        dst_pgs_thumbnail_path = dst_pgs_thumbnail_list[index]
                        self.rename(src_pgs_thumbnail_path,
                                    dst_pgs_thumbnail_path)
                    shutil.move(src_path, dst_path)

                else:
                    shutil.move(src_path, dst_path)

                return json.dumps(
                    {'status': get_return_code_by_error_definition("ORS_SUCCESS"), "message": "rename " + src_path + " to " + dst_path + " success"})

            except Exception as e:
                error_class = e.__class__.__name__  # 取得錯誤類型
                detail = e.args[0]  # 取得詳細內容
                cl, exc, tb = sys.exc_info()  # 取得Call Stack
                lastCallStack = traceback.extract_tb(
                    tb)[-1]  # 取得Call Stack的最後一筆資料
                fileName, lineNum, funcName = lastCallStack[0], lastCallStack[1], lastCallStack[2]
                errMsg = "File \"{}\", line {}, in {}: [{}] {}".format(
                    fileName, lineNum, funcName, error_class, detail)
                return json.dumps(
                    {'status': get_return_code_by_error_definition("ERR_FAIL_TO_WRITE"), "message": "rename " + src_path + " to " + dst_path + " fail " + errMsg})

    def get_refined_target_path(self, dst_path):
        src_dst_file_base_name, src_dst_file_base_extension = os.path.splitext(
            dst_path)
        copies_n = 1
        while os.path.exists(dst_path):
            new_dst_path = src_dst_file_base_name + "_copy_" + \
                str(copies_n) + src_dst_file_base_extension
            orisolLog(level="warning", module_name=Service_Name,
                      message="rename duplicate target name to " + new_dst_path)
            dst_path = new_dst_path
            copies_n = copies_n + 1
        return dst_path

    @sync_after_file_access
    def duplicate(self, src_path, dst_path, keep_file_header):
        if os.path.exists(src_path) is False:
            return json.dumps({'status': -1, "message": "source path %s not found" % (src_path)})

        else:
            is_src_dir = os.path.isdir(src_path)
            dst_dir = os.path.dirname(dst_path)

            if is_src_dir:
                try:
                    shutil.copytree(src_path, dst_path)
                    return json.dumps(
                        {'status': get_return_code_by_error_definition("ORS_SUCCESS"), "message": "duplicate dir " + src_path + " to " + dst_path + " success"})
                except:
                    return json.dumps(
                        {'status': get_return_code_by_error_definition("ERR_FAIL_TO_COPY"), "message": "duplicate dir " + src_path + " to " + dst_path + " fail"})
            else:
                try:
                    if os.path.exists(dst_dir) == False:
                        p = Path(dst_dir)
                        p.mkdir(parents=True)

                    # workaround for sure extension of PGS/JOB file are lower case
                    dst_path = str(dst_path).replace(".PGS", ".pgs")
                    dst_path = str(dst_path).replace(".JOB", ".job")

                    shutil.copyfile(src_path, dst_path)
                    str_extension = os.path.splitext(dst_path)[-1]

                    absolute_dst_file_path = os.path.abspath(dst_path)

                    if str_extension == ".job":
                        src_job_bundle_list = self.get_job_bundle_list(file_path=src_path)
                        dst_job_bundle_list = self.get_job_bundle_list(file_path=dst_path)
                        bundle_amount = len(src_job_bundle_list)
                        for i in range(0, bundle_amount):
                            src_bundle_file_path = src_job_bundle_list[i]
                            dst_bundle_file_path = dst_job_bundle_list[i]
                            if os.path.exists(src_bundle_file_path) == True:
                                shutil.copyfile(src_bundle_file_path, dst_bundle_file_path)

                    elif str_extension == ".pgs":
                        self._create_pgs_bundle(file_path=dst_path)
                        src_pgs_bundle_list = self.get_pgs_bundle_list(file_path=src_path)
                        dst_pgs_bundle_list = self.get_pgs_bundle_list(file_path=dst_path)
                        bundle_amount = len(src_pgs_bundle_list)
                        for i in range(0, bundle_amount):
                            src_bundle_file_path = src_pgs_bundle_list[i]
                            dst_bundle_file_path = dst_pgs_bundle_list[i]
                            if os.path.exists(src_bundle_file_path) == True:
                                shutil.copyfile(src_bundle_file_path, dst_bundle_file_path)
                        pgs_parser = PgsParser()
                        if keep_file_header == False:
                            pgs_parser.refine_label_by_file_path(file_path=dst_path)

                    return json.dumps(
                        {'status': get_return_code_by_error_definition("ORS_SUCCESS"), "message": "duplicate file " + src_path + " to " + dst_path + " success"})

                except Exception as e:
                    print(e)
                    return json.dumps(
                        {'status': get_return_code_by_error_definition("ERR_FAIL_TO_COPY"), "message": "duplicate file " + src_path + " to " + dst_path + " fail"})

    @sync_after_file_access
    def remove(self, path):
        if (os.path.exists(path)):
            if (os.path.isdir(path)):
                try:
                    shutil.rmtree(path)
                    return json.dumps({'status': get_return_code_by_error_definition("ORS_SUCCESS"), "message": "remove " + path + " success"})
                except:
                    return json.dumps({'status': get_return_code_by_error_definition("ERR_FAIL_TO_DELETE"), "message": "remove " + path + " fail"})
            else:
                try:
                    str_extension = os.path.splitext(path)[-1]
                    if str_extension == ".pgs":
                        absolute_path = os.path.abspath(path)
                        dst_pgs_bundle_list = self.get_pgs_bundle_list(path)
                        for pgs_bundle in dst_pgs_bundle_list:
                            self.remove(pgs_bundle)
                        dst_pgs_thumbnail_list = self.get_pgs_thumbnail_list(
                            path)
                        for pgs_thumbnail in dst_pgs_thumbnail_list:
                            self.remove(pgs_thumbnail)
                        # os.remove(path)

                    elif str_extension == ".job":
                        absolute_path = os.path.abspath(path)
                        dst_job_bundle_list = self.get_job_bundle_list(path)
                        for job_bundle in dst_job_bundle_list:
                            self.remove(job_bundle)
                        # os.remove(path)

                    else:
                        pass
                        # os.remove(path)
                    os.remove(path)

                    return json.dumps({'status': get_return_code_by_error_definition("ORS_SUCCESS"), "message": "remove " + path + " success"})

                except Exception as e:
                    print(e)
                    return json.dumps({'status': get_return_code_by_error_definition("ERR_FAIL_TO_DELETE"), "message": "remove " + path + " fail"})

        else:
            return json.dumps({'status': get_return_code_by_error_definition("ERR_FAIL_TO_FIND"), "message": "file %s not found" % (path)})

    def _getFileProfile(self, path):
        full_path = os.path.abspath(path)
        is_file = os.path.isfile(full_path)

        if is_file:
            file_type = 'unknown'
            if (os.path.splitext(full_path)[-1] == '.pgs'):
                file_type = 'PGS'
            elif (os.path.splitext(full_path)[-1] == '.job'):
                file_type = 'JOB'
            elif (os.path.splitext(full_path)[-1] == '.ppi'):
                file_type = 'PPI'
            else:
                file_type = str(os.path.splitext(full_path)[-1]).upper()[1:]
        else:
            file_type = 'DIR'

        modify_time = 0
        try:
            modify_time = os.path.getatime(full_path)
        except Exception as e:
            orisolLog(level='debug', module_name=Service_Name,
                      message='error:{}' .format(e))

        json_file_profile = {
            'file_name': os.path.split(path)[-1],
            'file_path': full_path,
            'is_file': os.path.isfile(full_path),
            'file_type': file_type,
            'modify_time': modify_time,
        }

        if file_type == 'PGS':
            json_file_profile['small_thumbnail_path'] = full_path.replace(
                ".pgs", "_s.png")
            json_file_profile['middle_thumbnail_path'] = full_path.replace(
                ".pgs", "_m.png")

        return json_file_profile

    def list_file(self, path, sort_key="file_path"):
        files = os.listdir(path)
        json_files = []

        for file in files:
            full_path = os.path.join(path, file)
            json_file_profile = self._getFileProfile(full_path)
            json_files.append(json_file_profile)
        json_files = sorted(
            json_files, key=lambda k: k[sort_key], reverse=False)

        return json_files

    def listFile(self, path, sort_key="file_path"):
        if (os.path.exists(path) == False):
            return json.dumps({'status': get_return_code_by_error_definition("ERR_FAIL_TO_FIND"), 'message': path + ' is not exist'})

        json_files = self.list_file(path=path, sort_key=sort_key)

        return json.dumps({'status': get_return_code_by_error_definition("ORS_SUCCESS"), 'message': json_files})

    def recursive_list_file(self, path):
        json_files = []
        # files = glob.glob(path, recursive=True)
        # for file in files:
        #     full_path = os.path.abspath(file)
        #     json_file_profile = self._getFileProfile(full_path)
        #     print(json_file_profile)
        #     json_files.append(json_file_profile)
        if os.path.isdir(path):
            for r, d, f in os.walk(path):
                for file in f:
                    full_path = os.path.join(r, file)
                    json_file_profile = self._getFileProfile(full_path)
                    json_files.append(json_file_profile)
        else:
            full_path = path
            json_file_profile = self._getFileProfile(full_path)
            json_files.append(json_file_profile)

        json_files = sorted(
            json_files, key=lambda k: k['file_path'], reverse=False)

        return json_files

    def recursiveListFile(self, path):
        if not os.path.exists(path):
            return json.dumps({'status': get_return_code_by_error_definition("ERR_FAIL_TO_FIND"), 'message': path + ' is not exist'})

        json_files = self.recursive_list_file(path=path)

        return json.dumps({'status': get_return_code_by_error_definition("ORS_SUCCESS"), 'list': json_files})

    def _list_blk_dev(self, default_usb_folder='Import'):
        cmd = 'lsblk --json -o model,ro,rm,mountpoint,hotplug'
        output, errors = self.execute_command(command=cmd)

        obj = json.loads(output)
        obj_blockdevs = obj['blockdevices']

        obj_hotplugdisks = []

        obj_hotplugdisks.append(
            {'label': 'local', 'mountpoint': local_root_folder_path})

        is_usb_storage_exist = False
        usb_device_index = 0
        for obj_blockdev in obj_blockdevs:
            if (obj_blockdev['mountpoint'] != None and obj_blockdev['ro'] == False and obj_blockdev['hotplug'] == True):
                # 找到的熱插拔設備, mount 路徑必須是以 UHF_Sys.ini [FilePath] OBSERVE_FOLDER 開頭
                if (obj_blockdev['mountpoint'].startswith(observe_folder_path)):
                    usb_path = obj_blockdev['mountpoint']
                    usb_import_path = os.path.join(usb_path, 'Import')
                    if not os.path.exists(usb_import_path):
                        self._create_directonary(
                            file_path=usb_import_path)
                    usb_dump_path = os.path.join(usb_path, 'DUMP')
                    if not os.path.exists(usb_dump_path):
                        self._create_directonary(
                            file_path=usb_dump_path)
                    usb_label = "USB%d" % (usb_device_index)
                    usb_device_index += 1
                    obj_hotplugdisk = {
                        'label': usb_label,
                        'mountpoint': usb_path
                    }
                    if str(usb_path).find('VBox') == -1:
                        obj_hotplugdisks.append(obj_hotplugdisk)
                        is_usb_storage_exist = True

        # create template fake usb folder is no usb storage device
        # if is_usb_storage_exist == False:
        #     usb_path = 'DATA/USB'
        #     if not os.path.exists(usb_path):
        #         self._create_directonary(file_path=usb_path)
        #     usb_import_path = os.path.join(usb_path, 'Import')
        #     if not os.path.exists(usb_import_path):
        #         self._create_directonary(
        #             file_path=usb_import_path)
        #     usb_dump_path = os.path.join(usb_path, 'DUMP')
        #     if not os.path.exists(usb_dump_path):
        #         self._create_directonary(
        #             file_path=usb_dump_path)
        #     obj_hotplugdisk = {
        #         'label': obj_blockdev['label'],
        #         'mountpoint': usb_path
        #     }
        #     obj_hotplugdisk = {
        #         'label': 'usb',
        #         'mountpoint': usb_path
        #     }
        #     obj_hotplugdisks.append(obj_hotplugdisk)

        return obj_hotplugdisks

    def getSoftwareUpdateImportFolder(self):
        obj_hotplugdisks = self._list_blk_dev(default_usb_folder='UPDATE')
        if len(obj_hotplugdisks) > 1:
            return json.dumps({'status': get_return_code_by_error_definition("ORS_SUCCESS"), 'mountpoint': obj_hotplugdisks[1]['mountpoint']})
        else:
            return json.dumps({'status': get_return_code_by_error_definition("ERR_FAIL_TO_FIND"), 'mountpoint': None})

    def getDumpExportFolder(self):
        obj_hotplugdisks = self._list_blk_dev(default_usb_folder='DUMP')
        if len(obj_hotplugdisks) > 1:
            return json.dumps({'status': get_return_code_by_error_definition("ORS_SUCCESS"), 'mountpoint': obj_hotplugdisks[1]['mountpoint']})
        else:
            return json.dumps({'status': get_return_code_by_error_definition("ERR_FAIL_TO_FIND"), 'mountpoint': None})

    def listBlkDev(self):
        obj_hotplugdisks = self._list_blk_dev()

        if len(obj_hotplugdisks) == 0:
            return json.dumps({'status': get_return_code_by_error_definition("ERR_FAIL_TO_FIND"), 'list': obj_hotplugdisks})
        else:
            return json.dumps({'status': get_return_code_by_error_definition("ORS_SUCCESS"), 'list': obj_hotplugdisks})

    def umount(self, mountpoint):
        cmd = "umount {}" .format(mountpoint)
        output, errors = self.execute_command(command=cmd)
        return json.dumps({'status': get_return_code_by_error_definition("ORS_SUCCESS"), 'message': ""})

    def umountAllUsb(self):
        settings = Settings()
        mountpoint_prefix = settings.getSysConfig()[
            "FilePath"]["OBSERVE_FOLDER"]
        mountpoint_prefix = mountpoint_prefix + "*"

        block_device_list = self._list_blk_dev()
        status = get_return_code_by_error_definition("ORS_SUCCESS")

        cmd = "echo orisol | sudo -S umount {}" .format(mountpoint_prefix)
        output, errors = self.execute_command(command=cmd)
        print("---->o:{}, e:{}" .format(output, errors))

        orisolLog(level='debug', module_name=Service_Name,
                  message='cmd:{} output:{} error:{}' .format(cmd, output, errors))

        if str(errors) != "":
            status = get_return_code_by_error_definition("ERR_FAIL_TO_DISABLE")
            orisolLog(level='error', module_name=Service_Name,
                      message='cmd:{} output:{} error:{}' .format(cmd, output, errors))
            return json.dumps({'status': status, 'message': ""})

        # cmd = "echo orisol | sudo -S rm {}" .format(mountpoint_prefix)
        # output, errors = self.execute_command(command=cmd)
        # print("---->o:{}, e:{}" .format(output, errors))

        # orisolLog(level='debug', module_name=Service_Name,
        #     message='cmd:{} output:{} error:{}' .format(cmd, output, error))

        # if str(errors) != "":
        #     status = get_return_code_by_error_definition("ERR_FAIL_TO_DISABLE")
        #     orisolLog(level='error', module_name=Service_Name,
        #         message='cmd:{} output:{} error:{}' .format(cmd, output, error))
        #     return json.dumps({'status': status, 'message': ""})

        return json.dumps({'status': status, 'message': ""})

    @sync_after_file_access
    def importOnsPathFile(self, path):
        external_storage_list = self._list_blk_dev()

        if len(external_storage_list) == 0:
            orisolLog(level='warning', module_name='API_Gateway',
                      message='no usb storage existed')
            src_folder_path = 'DATA/USB/Import'
            orisolLog(level="warning", module_name=Service_Name,
                      message="import from local:" + src_folder_path)
        else:
            src_folder_path = os.path.join(
                external_storage_list[0]['mountpoint'], "Import")

        orisolLog(level="debug", module_name=Service_Name,
                  message="import from:" + src_folder_path)

        json_files = self.recursive_list_file(path=src_folder_path)

        local_dst_folder = "import_" + datetime.datetime.now().strftime("%Y_%m_%d_%H_%M")

        for file in json_files:
            file_path = file['file_path']
            absolute_file_path = os.path.abspath(file_path)

            str_extension = os.path.splitext(file_path)[-1]
            if str_extension == ".pgs" or str_extension == ".job":
                if str_extension == ".pgs":
                    root_folder_path = prog_folder_path
                else:
                    root_folder_path = jobs_folder_path

                src_file_path = file['file_path']
                src_file_relative_path = os.path.relpath(
                    src_file_path, src_folder_path)
                dst_file_path = os.path.join(
                    root_folder_path, local_dst_folder, src_file_relative_path)
                dst_file_path = os.path.join(
                    root_folder_path, src_file_relative_path)
                dst_dir = os.path.dirname(dst_file_path)

                if os.path.exists(dst_dir) == False:
                    p = Path(dst_dir)
                    p.mkdir(parents=True)

                shutil.copyfile(src_file_path, dst_file_path)

        list_pgs = self.recursive_list_file(path=prog_folder_path)
        list_job = self.recursive_list_file(path=jobs_folder_path)

        return json.dumps({'status': get_return_code_by_error_definition("ORS_SUCCESS"), "message": "import files success"})

    @sync_after_file_access
    def exportOnsPathFile(self, path):
        external_storage_list = self._list_blk_dev()

        if len(external_storage_list) == 0:
            orisolLog(level='warning', module_name='API_Gateway',
                      message='no usb storage existed')
            dst_folder_path = 'DATA/USB/Import'
            orisolLog(level="warning", module_name=Service_Name,
                      message="export to local " + dst_folder_path)
        else:
            dst_folder_path = os.path.join(
                external_storage_list[0]['mountpoint'], "Import")

        orisolLog(level="debug", module_name=Service_Name,
                  message="export to " + dst_folder_path)

        json_files = self.recursive_list_file(path=path)
        list_job_reference = []
        for file in json_files:
            file_path = file['file_path']
            absolute_file_path = os.path.abspath(file_path)

            str_extension = os.path.splitext(file_path)[-1]
            if str_extension == ".pgs" or str_extension == ".job":
                if str_extension == ".pgs":
                    root_folder_path = prog_folder_path
                else:
                    root_folder_path = jobs_folder_path

                dst_file_relative_path = os.path.relpath(
                    file_path, root_folder_path)
                dst_file_path = os.path.join(
                    dst_folder_path, dst_file_relative_path)
                dst_dir = os.path.dirname(dst_file_path)

                src_file_path = file_path

                if os.path.exists(dst_dir) == False:
                    p = Path(dst_dir)
                    p.mkdir(parents=True)

                shutil.copyfile(src_file_path, dst_file_path)

        for reference in list_job_reference:
            root_folder_path = jobs_folder_path
            file_path = reference

            dst_file_relative_path = os.path.relpath(
                file_path, root_folder_path)
            dst_file_path = os.path.join(
                dst_folder_path, dst_file_relative_path)
            src_file_path = file_path

            shutil.copyfile(src_file_path, dst_file_path)

        return json.dumps({'status': get_return_code_by_error_definition("ORS_SUCCESS"), "message": "export files success"})

    @synchronized
    def listSmbServiceName(self):
        if self.smbclient.is_active() == False:
            return_code = get_return_code_by_error_definition(
                "ERR_FAIL_TO_CONNECT")
            return json.dumps({'status': return_code, 'message': 'smb_client inactive'})

        service_list = []
        return_code = 0
        conn_status = self.smbclient.connect()
        if conn_status:
            status, err_msg, service_list = self.smbclient.get_service_name()
            if status == True:
                self.smbclient.disconnect()

        if conn_status == False or status == False:
            return_code = get_return_code_by_error_definition(
                "ERR_FAIL_TO_CONNECT")
            return json.dumps({'status': return_code, 'message': err_msg})
        else:
            return_code = get_return_code_by_error_definition("ORS_SUCCESS")
            return json.dumps({'status': return_code, 'list_service_name': service_list})

    @synchronized
    def updateSmbSetting(self):
        update_settings = Settings()
        update_settings.loadSysConfig()
        update_settings.loadOnsMachine()
        share_folder_config = update_settings.getShareFolderConf()
        smb_server_ip = share_folder_config['Import_Network_IP_Address']
        smb_username = share_folder_config['Import_Network_Username']
        smb_password = share_folder_config['Import_Network_Password']
        smb_importfolder = share_folder_config['Import_Network_Shared_Directory']
        smb_exportfolder = share_folder_config['Export_Network_Shared_Directory']

        smb_folder = ''
        if smb_folder_path != None:
            if smb_folder_path != jobs_folder_path and smb_folder_path != prog_folder_path:
                smb_folder = os.path.join(os.getcwd(), smb_folder_path)
        self.smbclient.init_parameter(
            smb_server_ip, smb_username, smb_password, smb_importfolder, smb_exportfolder, smb_folder)

        self.smbclient_probe.init_parameter(
            smb_server_ip, smb_username, smb_password, smb_importfolder, smb_exportfolder, smb_folder)
        return json.dumps({'status': 0, 'message': ''})

    @synchronized
    def echoSmbService(self, echo_cmd):
        if self.smbclient.is_active() == False:
            return_code = get_return_code_by_error_definition(
                "ERR_FAIL_TO_CONNECT")
            return json.dumps({'status': return_code, 'message': 'smb_client inactive'})

        return_code = 0
        echo_feedback = ''
        err_msg = ''
        conn_status = self.smbclient.connect()
        if conn_status:
            status, err_msg, echo_feedback = self.smbclient.echo(data=echo_cmd)
            if status == True:
                self.smbclient.disconnect()

        if conn_status == False or status == False:
            return_code = get_return_code_by_error_definition(
                "ERR_FAIL_TO_CONNECT")
        else:
            return_code = get_return_code_by_error_definition("ORS_SUCCESS")
        return json.dumps({'status': return_code, 'message': err_msg})

    @synchronized
    def listSmbFileNames(self):
        if self.smbclient.is_active() == False:
            return_code = get_return_code_by_error_definition(
                "ERR_FAIL_TO_CONNECT")
            return json.dumps({'status': return_code, 'message': 'smb_client inactive'})

        return_code = 0
        conn_status = self.smbclient.connect()
        if conn_status:
            file_list = []
            status, err_msg, file_info_list = self.smbclient.get_file_list()

            for file_info in file_info_list:
                file_list.append(file_info)

            if status == True:
                self.smbclient.disconnect()

        if conn_status == False or status == False:
            return_code = get_return_code_by_error_definition(
                "ERR_FAIL_TO_CONNECT")
            return json.dumps({'status': return_code, 'message': err_msg})
        else:
            return_code = get_return_code_by_error_definition("ORS_SUCCESS")
            return json.dumps({'status': return_code, 'message': file_list})

    @synchronized
    def downloadSmbFiles(self, str_files):
        files = str_files.split(', ')
        if self.smbclient.is_active() == False:
            return_code = get_return_code_by_error_definition(
                "ERR_FAIL_TO_CONNECT")
            return json.dumps({'status': return_code, 'message': 'smb_client inactive'})

        return_code = 0
        filenames = []
        for file in files:
            filenames.append(file)

        conn_status = self.smbclient.connect()
        if conn_status:
            status, err_msg, dest_files = self.smbclient.download(
                filenames=filenames)

            if status == True:
                self.smbclient.disconnect()

        if conn_status == False or status == False:
            return_code = get_return_code_by_error_definition(
                "ERR_FAIL_TO_CONNECT")
            return json.dumps({'status': return_code, 'message': err_msg})
        else:
            return_code = get_return_code_by_error_definition("ORS_SUCCESS")

            for dest_file in dest_files:
                str_extension = os.path.splitext(dest_file)[-1]

                if str_extension == ".job":
                    pass

                elif str_extension == ".pgs":
                    self._create_pgs_bundle(file_path=dest_file)

            return json.dumps({'status': return_code})

    @synchronized
    def uploadSmbFiles(self, str_files):
        files = str_files.split(', ')
        if self.smbclient.is_active() == False:
            return_code = get_return_code_by_error_definition(
                "ERR_FAIL_TO_CONNECT")
            return json.dumps({'status': return_code, 'message': 'smb_client inactive'})

        return_code = 0
        filenames = []
        for file in files:
            filenames.append(file)

        conn_status = self.smbclient.connect()
        if conn_status:
            status, err_msg = self.smbclient.upload(filenames=filenames)
            if status == True:
                self.smbclient.disconnect()

        if conn_status == False or status == False:
            return_code = get_return_code_by_error_definition(
                "ERR_FAIL_TO_CONNECT")
            return json.dumps({'status': return_code, 'message': err_msg})
        else:
            return_code = get_return_code_by_error_definition("ORS_SUCCESS")
            return json.dumps({'status': return_code})

    @synchronized
    def deleteSmbFiles(self, str_files):
        files = str_files.split(', ')
        if self.smbclient.is_active() == False:
            return_code = get_return_code_by_error_definition(
                "ERR_FAIL_TO_CONNECT")
            return json.dumps({'status': return_code, 'message': 'smb_client inactive'})

        return_code = 0
        filenames = []
        for file in files:
            filenames.append(file)

        conn_status = self.smbclient.connect()
        if conn_status:
            status, err_msg = self.smbclient.rmfiles(filenames=filenames)

            if status == True:
                self.smbclient.disconnect()

        if conn_status == False or status == False:
            return_code = get_return_code_by_error_definition(
                "ERR_FAIL_TO_CONNECT")
            return json.dumps({'status': return_code, 'message': err_msg})
        else:
            return_code = get_return_code_by_error_definition("ORS_SUCCESS")
            return json.dumps({'status': return_code})

    @synchronized
    def renameSmbFiles(self, str_old_filenames, str_new_filenames):
        old_files = str_old_filenames.split(', ')
        new_files = str_new_filenames.split(', ')

        if self.smbclient.is_active() == False:
            return_code = get_return_code_by_error_definition(
                "ERR_FAIL_TO_CONNECT")
            return json.dumps({'status': return_code, 'message': 'smb_client inactive'})

        return_code = 0
        old_filenames = []
        new_filenames = []
        for file in old_files:
            old_filenames.append(file)
        for file in new_files:
            new_filenames.append(file)

        if len(old_filenames) != len(new_filenames):
            return_code = get_return_code_by_error_definition(
                "ERR_FAIL_TO_RENAME")
            err_msg = 'old_filenames new_filenames numbers must the same'
            return json.dumps({'status': return_code, 'message': err_msg})
        elif len(old_filenames) == 0 or len(new_filenames) == 0:
            return_code = get_return_code_by_error_definition(
                "ERR_FAIL_TO_RENAME")
            err_msg = 'old_filenames new_filenames numbers must > 0 '
            return json.dumps({'status': return_code, 'message': err_msg})
        conn_status = self.smbclient.connect()
        if conn_status:
            status, err_msg = self.smbclient.rename(
                old_filenames=old_filenames, new_filenames=new_filenames)

            if status == True:
                self.smbclient.disconnect()

        if conn_status == False or status == False:
            return_code = get_return_code_by_error_definition(
                "ERR_FAIL_TO_CONNECT")
            return json.dumps({'status': return_code, 'message': err_msg})
        else:
            return_code = get_return_code_by_error_definition("ORS_SUCCESS")
            return json.dumps({'status': return_code})

    @synchronized
    def createSmbDirectory(self, path):
        print('smb_impl-createSmbDirectory', path)
        if self.smbclient.is_active() == False:
            return_code = get_return_code_by_error_definition(
                "ERR_FAIL_TO_CONNECT")
            return json.dumps({'status': return_code, 'message': 'smb_client inactive'})

        return_code = 0
        err_msg = ''
        conn_status = self.smbclient.connect()
        if conn_status:
            status, err_msg = self.smbclient.create_directory(path)
            if status == True:
                self.smbclient.disconnect()

        if conn_status == False or status == False:
            return_code = get_return_code_by_error_definition(
                "ERR_FAIL_TO_CONNECT")
        else:
            return_code = get_return_code_by_error_definition("ORS_SUCCESS")
        return json.dumps({'status': return_code, 'message': err_msg})

    @synchronized
    def deleteSmbDirectory(self, path):
        print('smb_impl-deleteSmbDirectory', path)
        if self.smbclient.is_active() == False:
            return_code = get_return_code_by_error_definition(
                "ERR_FAIL_TO_CONNECT")
            return json.dumps({'status': return_code, 'message': 'smb_client inactive'})

        return_code = 0
        err_msg = ''
        conn_status = self.smbclient.connect()
        if conn_status:
            status, err_msg = self.smbclient.delete_directory(path)
            if status == True:
                self.smbclient.disconnect()

        if conn_status == False or status == False:
            return_code = get_return_code_by_error_definition(
                "ERR_FAIL_TO_CONNECT")
        else:
            return_code = get_return_code_by_error_definition("ORS_SUCCESS")
        return json.dumps({'status': return_code, 'message': err_msg})

    def enableSmbProbe(self, command):
        smb_probe = SmbProbe()
        smb_probe.enable(command)
        return_code = get_return_code_by_error_definition("ORS_SUCCESS")
        return json.dumps({'status': return_code})

    def mount(self, type, options, source, target):
        cmd = "mount"
        if type != "":
            cmd = "{} -t {}" .format(cmd, type)
        if options != "":
            cmd = "{} -o {}" .format(cmd, options)
        cmd = "{} {} {}" .format(cmd, source, target)
        output, error = self.execute_command(command=cmd)

        message = ""
        return_code = get_return_code_by_error_definition("ORS_SUCCESS")
        if error != "":
            message = error
            return_code = get_return_code_by_error_definition(
                "ERR_FAIL_TO_BIND")
        return json.dumps({'status': return_code, 'message': message})

    def getMountStatus(self, mountpoint):
        cmd = "mountpoint {}" .format(mountpoint)
        output, error = self.execute_command(command=cmd)
        is_mounted = str(output).find("is a mountpoint") != -1
        message = {
            "is_mounted": is_mounted
        }
        return json.dumps({'status': get_return_code_by_error_definition("ORS_SUCCESS"), 'message': message})

    def generatePgsThumbnail(self, path):
        self._create_pgs_bundle(file_path=path)
        return json.dumps({'status': get_return_code_by_error_definition("ORS_SUCCESS"), 'message': ""})

    @synchronized
    def connectLoginFtp(self):
        ftp_code = {'550': get_return_code_by_error_definition(
            "ERR_FTP_CLIENT_DIRECTORY_NOT_FOUND")}
        welcome = ''

        if ftp_probe_event.isSet():
            return_code = get_return_code_by_error_definition("ERR_FTP_CLIENT_BLOCKING")
            return json.dumps({"status": return_code, "message": "Wait FtpProbe"})

        status, msg, welcome = self.ftpclient.remote_open()
        if status == -1:
            return_code = get_return_code_by_error_definition(
                "ERR_FTP_CLIENT_CONNECT_TIMEOUT")
            message = msg
            for code in ftp_code:
                if code in msg:
                    return_code = ftp_code[code]
        else:
            return_code = get_return_code_by_error_definition("ORS_SUCCESS")
            message = welcome
        return json.dumps({"status": return_code, "message": message})

    @synchronized
    def enableFtpProbe(self, en):
        if en == True:
            if ftp_probe_event.isSet():
                return_code = get_return_code_by_error_definition("ERR_FTP_CLIENT_BLOCKING")
                return json.dumps({"status": return_code, "message": "Wait FtpProbe"})
            self.ftpserviceprobe.ftp_probe_start()
        else:
            self.ftpserviceprobe.ftp_probe_pause()
        return json.dumps({"status": 0, "message": ''})

    @synchronized
    def listFtp(self, search_path=None):
        if ftp_probe_event.isSet():
            return_code = get_return_code_by_error_definition("ERR_FTP_CLIENT_BLOCKING")
            return json.dumps({"status": return_code, "message": "Wait FtpProbe"})

        if self.ftpclient.Is_timeout_reconnect() == True:
            status, msg, welcome = self.ftpclient.remote_open(
                change_to_backup=True)
            if status == -1:
                return_code = get_return_code_by_error_definition(
                    "ERR_FAIL_TO_CONNECT")
                return json.dumps({"status": return_code, "message": msg})

        status, msg = self.ftpclient.remote_get_file_list(search_path)
        if status == -1:
            return_code = get_return_code_by_error_definition(
                "ERR_FAIL_TO_EXECUTE")
        else:
            return_code = get_return_code_by_error_definition("ORS_SUCCESS")
        return json.dumps({"status": return_code, "message": msg})

    @synchronized
    def pwdFtp(self):
        if ftp_probe_event.isSet():
            return_code = get_return_code_by_error_definition("ERR_FTP_CLIENT_BLOCKING")
            return json.dumps({"status": return_code, "message": "Wait FtpProbe"})

        if self.ftpclient.Is_timeout_reconnect() == True:
            status, msg, welcome = self.ftpclient.remote_open(
                change_to_backup=True)
            if status == -1:
                return_code = get_return_code_by_error_definition(
                    "ERR_FAIL_TO_CONNECT")
                return json.dumps({"status": return_code, "message": msg})

        # status, msg = self.ftpclient.remote_pwd()
        # if status == -1:
        #     return_code = get_return_code_by_error_definition("ERR_FAIL_TO_EXECUTE")
        # else:
        #     return_code = get_return_code_by_error_definition("ORS_SUCCESS")
        # return json.dumps({"status": return_code, "message": msg})

        folder_info = self.ftpclient.get_remote_mountpoint_info()
        return_code = get_return_code_by_error_definition("ORS_SUCCESS")
        return json.dumps({"status": return_code, "message": folder_info})

    @synchronized
    def cwdFtp(self, working_directory):
        if ftp_probe_event.isSet():
            return_code = get_return_code_by_error_definition("ERR_FTP_CLIENT_BLOCKING")
            return json.dumps({"status": return_code, "message": "Wait FtpProbe"})

        if self.ftpclient.Is_timeout_reconnect() == True:
            status, msg, welcome = self.ftpclient.remote_open(
                change_to_backup=True)
            if status == -1:
                return_code = get_return_code_by_error_definition(
                    "ERR_FAIL_TO_CONNECT")
                return json.dumps({"status": return_code, "message": msg})
        status, msg = self.ftpclient.remote_change_current_folder(
            cwd=working_directory)
        if status == -1:
            return_code = get_return_code_by_error_definition(
                "ERR_FAIL_TO_EXECUTE")
        else:
            return_code = get_return_code_by_error_definition("ORS_SUCCESS")
        return json.dumps({"status": return_code, "message": msg})

    @synchronized
    def mkdirFtp(self, mountpoint, folder_name):
        if ftp_probe_event.isSet():
            return_code = get_return_code_by_error_definition("ERR_FTP_CLIENT_BLOCKING")
            return json.dumps({"status": return_code, "message": "Wait FtpProbe"})

        path_name = ''
        if self.ftpclient.Is_timeout_reconnect() == True:
            status, msg, welcome = self.ftpclient.remote_open(
                change_to_backup=True)
            if status == -1:
                return_code = get_return_code_by_error_definition(
                    "ERR_FAIL_TO_CONNECT")
                return json.dumps({"status": return_code, "message": msg})

        folder_info = self.ftpclient.get_remote_mountpoint_info()
        if mountpoint=='':
            path_name = os.path.join(folder_info['ftp_mountpoint'],folder_name)
        else:
            path_name = os.path.join(mountpoint,folder_name)

        status, msg = self.ftpclient.remote_mkdir(path_name=path_name)
        if status == -1:
            return_code = get_return_code_by_error_definition(
                "ERR_FAIL_TO_EXECUTE")
        else:
            return_code = get_return_code_by_error_definition("ORS_SUCCESS")
        return json.dumps({"status": return_code, "message": msg})

    @synchronized
    def rmdirFtp(self, folder_name):
        if ftp_probe_event.isSet():
            return_code = get_return_code_by_error_definition("ERR_FTP_CLIENT_BLOCKING")
            return json.dumps({"status": return_code, "message": "Wait FtpProbe"})

        if self.ftpclient.Is_timeout_reconnect() == True:
            status, msg, welcome = self.ftpclient.remote_open(
                change_to_backup=True)
            if status == -1:
                return_code = get_return_code_by_error_definition(
                    "ERR_FAIL_TO_CONNECT")
                return json.dumps({"status": return_code, "message": msg})

        status, msg = self.ftpclient.remote_rmdir(path_name=folder_name)
        if status == -1:
            return_code = get_return_code_by_error_definition(
                "ERR_FAIL_TO_EXECUTE")
        else:
            return_code = get_return_code_by_error_definition("ORS_SUCCESS")
        return json.dumps({"status": return_code, "message": msg})

    @synchronized
    def changeUpParentDirFtp(self):
        if ftp_probe_event.isSet():
            return_code = get_return_code_by_error_definition("ERR_FTP_CLIENT_BLOCKING")
            return json.dumps({"status": return_code, "message": "Wait FtpProbe"})

        if self.ftpclient.Is_timeout_reconnect() == True:
            status, msg, welcome = self.ftpclient.remote_open(
                change_to_backup=True)
            if status == -1:
                return_code = get_return_code_by_error_definition(
                    "ERR_FAIL_TO_CONNECT")
                return json.dumps({"status": return_code, "message": msg})

        status, msg = self.ftpclient.remote_change_up_current_folder()
        if status == -1:
            return_code = get_return_code_by_error_definition(
                "ERR_FAIL_TO_EXECUTE")
        else:
            return_code = get_return_code_by_error_definition("ORS_SUCCESS")
        return json.dumps({"status": return_code, "message": msg})

    @synchronized
    def downloadFtp(self, mount, filelist):
        if ftp_probe_event.isSet():
            return_code = get_return_code_by_error_definition("ERR_FTP_CLIENT_BLOCKING")
            return json.dumps({"status": return_code, "message": "Wait FtpProbe"})

        mount_dict = json.loads(mount)
        filelist_list = json.loads(filelist)
        b_check = True
        b_check &= 'src_mount' in mount_dict
        b_check &= 'dst_mount' in mount_dict
        for n_file in filelist_list:
            b_check &= 'src_filename' in n_file
        if b_check == False:
            return_code = get_return_code_by_error_definition(
                "ERR_INVALID_FORMAT")
            return json.dumps({"status": return_code, "message": "field is not in request body"})

        if (os.path.exists(mount_dict['dst_mount'])) == False:
            return_code = get_return_code_by_error_definition(
                "ERR_FAIL_TO_FIND")
            return json.dumps({"status": return_code, "message": "dst mount not found"})

        src_filename = []
        dst_filename = []
        for n_file in filelist_list:
            src_filename.append(n_file['src_filename'])
            if 'dst_filename' in n_file:
                dst_filename.append(n_file['dst_filename'])
            else:
                dst_filename.append(n_file['src_filename'])
        self.ftpclient.set_bulktag_info(src_mount=mount_dict['src_mount'],
                                        dst_mount=mount_dict['dst_mount'],
                                        src_filenamelist=src_filename,
                                        dst_filenamelist=dst_filename)
        download_status, escape, msg = self.ftpclient.bulk_download()
        if download_status == -1:
            return_code = get_return_code_by_error_definition(
                "ERR_FAIL_TO_CONNECT")
            return json.dumps({"status": return_code, "message": msg})
        else:
            return_code = get_return_code_by_error_definition("ORS_SUCCESS")
            return json.dumps({"status": return_code, "message": 'success, escape:%s' % (escape)})

    @synchronized
    def uploadFtp(self, mount, filelist):
        if ftp_probe_event.isSet():
            return_code = get_return_code_by_error_definition("ERR_FTP_CLIENT_BLOCKING")
            return json.dumps({"status": return_code, "message": "Wait FtpProbe"})

        mount_dict = json.loads(mount)
        filelist_list = json.loads(filelist)
        b_check = True
        b_check &= 'src_mount' in mount_dict
        b_check &= 'dst_mount' in mount_dict
        for n_file in filelist_list:
            b_check &= 'src_filename' in n_file
        if b_check == False:
            return_code = get_return_code_by_error_definition(
                "ERR_INVALID_FORMAT")
            return json.dumps({"status": return_code, "message": "field is not in request body"})

        src_filename = []
        dst_filename = []
        for n_file in filelist_list:
            src_filename.append(n_file['src_filename'])
            if 'dst_filename' in n_file:
                dst_filename.append(n_file['dst_filename'])
            else:
                dst_filename.append(n_file['src_filename'])
        self.ftpclient.set_bulktag_info(src_mount=mount_dict['src_mount'],
                                        dst_mount=mount_dict['dst_mount'],
                                        src_filenamelist=src_filename,
                                        dst_filenamelist=dst_filename)
        download_status, escape, msg = self.ftpclient.bulk_upload()
        if download_status == -1:
            return_code = get_return_code_by_error_definition(
                "ERR_FAIL_TO_CONNECT")
            return json.dumps({"status": return_code, "message": msg})
        else:
            return_code = get_return_code_by_error_definition("ORS_SUCCESS")
            return json.dumps({"status": return_code, "message": 'success, escape:%s' % (escape)})

    @synchronized
    def deleteFtp(self, mount, filelist):
        if ftp_probe_event.isSet():
            return_code = get_return_code_by_error_definition("ERR_FTP_CLIENT_BLOCKING")
            return json.dumps({"status": return_code, "message": "Wait FtpProbe"})

        mount_dict = json.loads(mount)
        filelist_list = json.loads(filelist)
        b_check = True
        b_check &= 'remote_mount' in mount_dict
        for n_file in filelist_list:
            b_check &= 'remote_filename' in n_file
        if b_check == False:
            return_code = get_return_code_by_error_definition(
                "ERR_INVALID_FORMAT")
            return json.dumps({"status": return_code, "message": "field is not in request body"})
        dst_filename = []
        for n_file in filelist_list:
            dst_filename.append(n_file['remote_filename'])
        self.ftpclient.set_bulktag_info(dst_mount=mount_dict['remote_mount'],
                                        dst_filenamelist=dst_filename)
        delete_status, escape, msg = self.ftpclient.bulk_delete()
        if delete_status == -1:
            return_code = get_return_code_by_error_definition(
                "ERR_FAIL_TO_CONNECT")
            return json.dumps({"status": return_code, "message": msg})
        else:
            return_code = get_return_code_by_error_definition("ORS_SUCCESS")
            return json.dumps({"status": return_code, "message": 'success, escape:%s' % (escape)})

    @synchronized
    def renameFtp(self, renamelist):
        if ftp_probe_event.isSet():
            return_code = get_return_code_by_error_definition("ERR_FTP_CLIENT_BLOCKING")
            return json.dumps({"status": return_code, "message": "Wait FtpProbe"})

        filelist_list = json.loads(renamelist)
        b_check = True
        for n_file in filelist_list:
            b_check &= 'old_name' in n_file
            b_check &= 'new_name' in n_file
        if b_check == False:
            return_code = get_return_code_by_error_definition(
                "ERR_INVALID_FORMAT")
            return json.dumps({"status": return_code, "message": "field is not in request body"})
        oldnamelist = []
        newnamelist = []
        for n_file in filelist_list:
            oldnamelist.append(n_file['old_name'])
            newnamelist.append(n_file['new_name'])
        self.ftpclient.set_bulktag_info(src_filenamelist=oldnamelist,
                                        dst_filenamelist=newnamelist)
        delete_status, escape, msg = self.ftpclient.bulk_rename()
        if delete_status == -1:
            return_code = get_return_code_by_error_definition(
                "ERR_FAIL_TO_CONNECT")
            return json.dumps({"status": return_code, "message": msg})
        else:
            return_code = get_return_code_by_error_definition("ORS_SUCCESS")
            return json.dumps({"status": return_code, "message": 'success, escape:%s' % (escape)})

    @synchronized
    def recursiveUploadFtp(self, src_path, dst_path):
        if ftp_probe_event.isSet():
            return_code = get_return_code_by_error_definition("ERR_FTP_CLIENT_BLOCKING")
            return json.dumps({"status": return_code, "message": "Wait FtpProbe"})

        src_path = json.loads(src_path)
        dst_path = json.loads(dst_path)
        b_check = True
        b_check &= 'local_mount' in src_path
        b_check &= 'remote_mount' in dst_path
        if b_check == False:
            return_code = get_return_code_by_error_definition(
                "ERR_INVALID_FORMAT")
            return json.dumps({"status": return_code, "message": "field is not in request body"})

        status, escape, msg = self.ftpclient.recursively_upload(
            src=src_path['local_mount'], dst=dst_path['remote_mount'])
        if status == -1:
            return_code = get_return_code_by_error_definition(
                "ERR_FAIL_TO_EXECUTE")
            return json.dumps({"status": return_code, "message": msg})
        else:
            return_code = get_return_code_by_error_definition("ORS_SUCCESS")
            return json.dumps({"status": return_code, "message": 'success, escape:%s' % (escape)})

    @synchronized
    def recursiveDownloadFtp(self, src_path, dst_path):
        if ftp_probe_event.isSet():
            return_code = get_return_code_by_error_definition("ERR_FTP_CLIENT_BLOCKING")
            return json.dumps({"status": return_code, "message": "Wait FtpProbe"})

        src_path = json.loads(src_path)
        dst_path = json.loads(dst_path)
        b_check = True
        b_check &= 'remote_mount' in src_path
        b_check &= 'local_mount' in dst_path
        if b_check == False:
            return_code = get_return_code_by_error_definition(
                "ERR_INVALID_FORMAT")
            return json.dumps({"status": return_code, "message": "field is not in request body"})

        status, escape, msg = self.ftpclient.recursively_download(
            src=src_path['remote_mount'], dst=dst_path['local_mount'])
        if status == -1:
            return_code = get_return_code_by_error_definition(
                "ERR_FAIL_TO_EXECUTE")
            return json.dumps({"status": return_code, "message": msg})
        else:
            return_code = get_return_code_by_error_definition("ORS_SUCCESS")
            return json.dumps({"status": return_code, "message": 'success, escape:%s' % (escape)})

    @synchronized
    def recursiveRemoveFtp(self, dst_path):
        if ftp_probe_event.isSet():
            return_code = get_return_code_by_error_definition("ERR_FTP_CLIENT_BLOCKING")
            return json.dumps({"status": return_code, "message": "Wait FtpProbe"})

        dst_path = json.loads(dst_path)
        b_check = True
        b_check &= 'remote_mount' in dst_path
        if b_check == False:
            return_code = get_return_code_by_error_definition(
                "ERR_INVALID_FORMAT")
            return json.dumps({"status": return_code, "message": "field is not in request body"})

        status, escape, msg = self.ftpclient.recursively_remove(
            dst=dst_path['remote_mount'])
        if status == -1:
            return_code = get_return_code_by_error_definition(
                "ERR_FAIL_TO_EXECUTE")
            return json.dumps({"status": return_code, "message": msg})
        else:
            return_code = get_return_code_by_error_definition("ORS_SUCCESS")
            return json.dumps({"status": return_code, "message": 'success, escape:%s' % (escape)})

    @synchronized
    def initrefreshFtp(self):
        settings = Settings()
        ftp_params = settings.getFtpClientConf(refresh=True)
        self.ftpclient.init_parameter(
            host=ftp_params['FTP_Server_IP'],
            username=ftp_params['FTP_Server_User_Name'],
            password=ftp_params['FTP_Server_User_Password'],
            local_current_directory=local_root_folder_path,
            local_tmp_directory=self.ftp_tmp_folder,
            port=int(ftp_params['FTP_Port']),
            implicit_TLS=int(ftp_params['Is_Implicit_TLS']) == 1,
            secure=int(ftp_params['Is_FTPS']) == 1,
            mountpoint=ftp_params['FTP_Mountpoint'],
            connect_timeout=int(ftp_params['FTP_Connect_Timeout']),
        )
        probe_params= copy.deepcopy(ftp_params)
        probe_params['local_root_folder_path'] = local_root_folder_path
        probe_params['ftp_tmp_folder'] = self.ftp_tmp_folder
        self.ftpserviceprobe.init_parameter(probeconfig=probe_params)
        return_code = get_return_code_by_error_definition("ORS_SUCCESS")
        return json.dumps({"status": return_code, "message": 'success'})

    @synchronized
    def relativeDownloadFtp(self, mountpoints, filepaths):
        if ftp_probe_event.isSet():
            return_code = get_return_code_by_error_definition("ERR_FTP_CLIENT_BLOCKING")
            return json.dumps({"status": return_code, "message": "Wait FtpProbe"})

        mountpoints_dict = json.loads(mountpoints)
        filepaths_list = json.loads(filepaths)
        b_check = True
        b_check &= 'src_mountpoint' in mountpoints_dict
        b_check &= 'dst_mountpoint' in mountpoints_dict
        for n_file in filepaths_list:
            b_check &= 'file_path' in n_file
        if b_check == False:
            return_code = get_return_code_by_error_definition(
                "ERR_INVALID_FORMAT")
            return json.dumps({"status": return_code, "message": "field is not in request body"})

        filepathlist = []
        for n_file in filepaths_list:
            filepathlist.append(n_file['file_path'])

        self.ftpclient.remote_open(change_to_backup=True)
        status, msg = self.ftpclient.remote_relative_download(filepathlist,
                                                              src_mountpoint=mountpoints_dict['src_mountpoint'],
                                                              dst_mountpoint=mountpoints_dict['dst_mountpoint'])
        if status == -1:
            return_code = get_return_code_by_error_definition(
                "ERR_FAIL_TO_CONNECT")
            return json.dumps({"status": return_code, "message": msg})
        else:
            return_code = get_return_code_by_error_definition("ORS_SUCCESS")
            return json.dumps({"status": return_code, "message": 'success'})

    @synchronized
    def relativeUploadFtp(self, mountpoints, filepaths):
        if ftp_probe_event.isSet():
            return_code = get_return_code_by_error_definition("ERR_FTP_CLIENT_BLOCKING")
            return json.dumps({"status": return_code, "message": "Wait FtpProbe"})

        mountpoints_dict = json.loads(mountpoints)
        filepaths_list = json.loads(filepaths)
        b_check = True
        b_check &= 'src_mountpoint' in mountpoints_dict
        b_check &= 'dst_mountpoint' in mountpoints_dict
        for n_file in filepaths_list:
            b_check &= 'file_path' in n_file
        if b_check == False:
            return_code = get_return_code_by_error_definition(
                "ERR_INVALID_FORMAT")
            return json.dumps({"status": return_code, "message": "field is not in request body"})

        src_filepaths = []
        for n_file in filepaths_list:
            src_filepaths.append(n_file['file_path'])
        self.ftpclient.remote_open(change_to_backup=True)
        status, msg = self.ftpclient.remote_relative_upload(src_filepaths=src_filepaths,
                                                            src_mountpoint=mountpoints_dict['src_mountpoint'],
                                                            dst_mountpoint=mountpoints_dict['dst_mountpoint'])
        if status == -1:
            return_code = get_return_code_by_error_definition(
                "ERR_FAIL_TO_CONNECT")
            return json.dumps({"status": return_code, "message": msg})
        else:
            return_code = get_return_code_by_error_definition("ORS_SUCCESS")
            return json.dumps({"status": return_code, "message": 'success'})

class SmbProbe(threading.Thread):
    def __init__(self):
        super(SmbProbe, self).__init__()

    def __new__(cls, *args, **kw):
        if not hasattr(cls, "_instance"):
            cls._instance = super(SmbProbe,
                                  cls).__new__(cls, *args, **kw)
        return cls._instance

    def initParam(self):
        self.smb_echo_data = 'smb_echo'
        self.service_name = ''
        self.service_path = ''
        self.smb_folder_path = ''
        self.root = '/'
        self.search_path = ''
        self.pgs_filelist = []
        self.job_filelist = []
        self.is_enable = False
        self.interval = 1
        self.active = False
        self.cond = threading.Condition()

    def init_parameter(self, ip, username, password, import_dir, export_dir, smb_folder_path, port=445):
        self.ip = ip
        self.username = username
        self.password = password

        _import_service = import_dir.split('/', 1)
        self.service_name = _import_service[0]
        self.service_path = '/'
        if len(_import_service) > 1 and _import_service[1] != '':
            self.service_path = _import_service[1]

        self.export_dir = export_dir
        self.port = port
        self.smb_folder_path = smb_folder_path
        self.conn_timeout = 5
        self.data_timeout = 10
        self.conn = None
        self.status = False
        self.init = True
        self.smb_echo_error_log = False
        if self.ip != '' and self.username != '' and self.password != '' and self.service_name != '' and self.smb_folder_path != '':
            self.active = True
        self.smbprobe = SmbClient('smbprobe')
        self.smbprobe.init_parameter(
            self.ip, self.username, self.password, import_dir,  self.export_dir,  self.smb_folder_path)
        return 0

    def enable(self, command):
        status = isinstance(command, bool)
        if isinstance(command, bool):
            self.is_enable = command
            self.interval = 1
            if self.is_enable == True:
                self.cond.acquire()
                self.cond.notify()
                self.cond.release()

    def is_active(self):
        return self.active

    @synchronized
    def smbprobeEcho(self):
        if self.is_active() == False:
            return_code = get_return_code_by_error_definition(
                "ERR_FAIL_TO_CONNECT")
            return json.dumps({'status': return_code, 'message': 'smb_client inactive'})
        return_code = 0
        echo_feedback = ''
        err_msg = ''
        conn_status = self.smbprobe.connect()
        if conn_status:
            status, err_msg, echo_feedback = self.smbprobe.echo(
                data=self.smb_echo_data)
            if status == True:
                self.smbprobe.disconnect()
        if conn_status == False or status == False:
            return_code = get_return_code_by_error_definition(
                "ERR_FAIL_TO_CONNECT")
        else:
            return_code = get_return_code_by_error_definition("ORS_SUCCESS")

        return json.dumps({'status': return_code, 'message': err_msg})

    @synchronized
    def smbprobeServiceName(self):
        if self.is_active() == False:
            return_code = get_return_code_by_error_definition(
                "ERR_FAIL_TO_CONNECT")
            return json.dumps({'status': return_code, 'message': 'smb_client inactive'})

        service_list = []
        return_code = 0
        conn_status = self.smbprobe.connect()
        if conn_status:
            status, err_msg, service_list = self.smbprobe.get_service_name()
            if status == True:
                self.smbprobe.disconnect()
        if conn_status == False or status == False:
            return_code = get_return_code_by_error_definition(
                "ERR_FAIL_TO_CONNECT")
            return json.dumps({'status': return_code, 'message': err_msg})
        else:
            return_code = get_return_code_by_error_definition("ORS_SUCCESS")
            return json.dumps({'status': return_code, 'list_service_name': service_list})

    def run(self):
        while True:
            if self.is_enable == False:
                self.cond.acquire()
                self.cond.wait()
                self.cond.release()

            _ret = self.smbprobeEcho()
            msg = json.loads(_ret)
            log = '%s,echo status:%d' % (__name__, msg['status'])
            orisolLog(level='info', module_name='SmbProbe', message=log)

            smb_probe_status = False
            self.interval = 1
            if msg['status'] == get_return_code_by_error_definition("ORS_SUCCESS"):
                _ret = self.smbprobeServiceName()
                msg = json.loads(_ret)
                if msg['status'] == get_return_code_by_error_definition("ORS_SUCCESS"):
                    if self.service_name != '' and self.service_name in msg['list_service_name'] and self.smb_folder_path != '':
                        smb_probe_status = True
                        self.interval = 10

            smb_probe_info = {'status': smb_probe_status,
                              'smb_folder_path': self.smb_folder_path}
            msg = json.dumps(smb_probe_info)
            report_smb_message_to_ui(
                message=msg, status="smb_probe_info",
                error_code=get_return_code_by_error_definition("ORS_SUCCESS"))

            time.sleep(self.interval)

class FtpServiceProbe(threading.Thread):
    def __init__(self):
        super(FtpServiceProbe, self).__init__()

    def __new__(cls, *args, **kw):
        if not hasattr(cls, "_instance"):
            cls._instance = super(FtpServiceProbe,
                                  cls).__new__(cls, *args, **kw)
        return cls._instance

    def init_parameter(self, probeconfig):
        self.connect_retry = int(probeconfig['FTP_Connect_Retry'])
        self.connect_count = 0
        self.i_interval = 1
        self.probe_en = False
        self.b_Is_SFTP = int(probeconfig['Is_SFTP']) == 1
        if self.b_Is_SFTP:
            self.ftp_client = SSH_Ftp_Client()
        else:
            self.ftp_client = FtpClient()

        self.ftp_client.init_parameter(
            host=probeconfig['FTP_Server_IP'],
            username=probeconfig['FTP_Server_User_Name'],
            password=probeconfig['FTP_Server_User_Password'],
            local_current_directory=probeconfig['local_root_folder_path'],
            local_tmp_directory=probeconfig['ftp_tmp_folder'],
            port=int(probeconfig['FTP_Port']),
            implicit_TLS=int(probeconfig['Is_Implicit_TLS']) == 1,
            secure=int(probeconfig['Is_FTPS']) == 1,
            mountpoint=probeconfig['FTP_Mountpoint'],
            connect_timeout=int(probeconfig['FTP_Connect_Timeout']),
        )

    def ftp_probe_start(self):
        self.probe_en = True
        self.connect_count = 0
        ftp_probe_event.set()

    def ftp_probe_pause(self):
        self.probe_en = False

    def run(self):
        ftp_code = {'550': get_return_code_by_error_definition(
            "ERR_FTP_CLIENT_DIRECTORY_NOT_FOUND")}
        welcome = ''

        while True:
            ftp_probe_event.clear()
            ftp_probe_event.wait()

            while (self.connect_count < self.connect_retry):
                self.connect_count += 1

                if self.probe_en == False:
                    break
                status, msg, welcome = self.ftp_client.remote_open()
                ftp_mountpoint_info = self.ftp_client.get_remote_mountpoint_info()
                if status == 0:
                    if self.b_Is_SFTP:
                        status = self.ftp_client.remote_path_exists(ftp_mountpoint_info['ftp_mountpoint'])
                        if status == False:
                            if self.connect_count == self.connect_retry:
                                return_code = get_return_code_by_error_definition(
                                    "ERR_FTP_CLIENT_DIRECTORY_NOT_FOUND")
                                message = 'Path not found'
                                report_ftp_message_to_ui(
                                    message=message, error_code=return_code)
                            else:
                                time.sleep(self.i_interval)
                            continue
                        else:
                            return_code = get_return_code_by_error_definition(
                                "ORS_SUCCESS")
                            report_ftp_message_to_ui(
                                message=welcome, error_code=return_code)
                            break

                    else:
                        status, files = self.ftp_client.remote_get_file_list()
                        if status == -1:
                            if self.connect_count == self.connect_retry:
                                return_code = get_return_code_by_error_definition(
                                    "ERR_FTP_CLIENT_CONNECT_TIMEOUT")
                                message = msg
                                for code in ftp_code:
                                    if code in msg:
                                        return_code = ftp_code[code]
                                report_ftp_message_to_ui(
                                    message=message, error_code=return_code)
                            else:
                                time.sleep(self.i_interval)
                            continue
                        else:
                            return_code = get_return_code_by_error_definition(
                                "ORS_SUCCESS")
                            report_ftp_message_to_ui(
                                message=welcome, error_code=return_code)
                            break
