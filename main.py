import sys
import traceback
from concurrent.futures._base import CancelledError
from pathlib import Path

try:
    from Crypto.Cipher import AES
except ImportError:
    from Cryptodome.Cipher import AES
from PyQt5 import QtWidgets
from urllib3 import disable_warnings

from ui import Ui_Form
import os
import time
from urllib.parse import urlparse
import aiohttp
import asyncio
from queue import Queue, Empty
import requests
from PyQt5.QtCore import QThread, pyqtSignal, QObject
import re

disable_warnings()


def req_url(url):
    print(url)
    try:
        data = requests.get(url=url, timeout=10, verify=False, headers={
            'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/83.0.4103.61 Safari/537.36'})
        if data.status_code != 200:
            raise Exception('{}'.format(data.status_code))
        return data
    except Exception as e:
        print('请求失败， {}, {}'.format(url, e))
        return None


class MyDownloadUi(Ui_Form):
    def __init__(self):
        super(MyDownloadUi, self).__init__()
        self.is_start = False
        self.log_queue = Queue()
        self.task_count = Queue()
        self.back_ground_task = None

    def set_action(self):
        self.startButton.clicked.connect(self.start_btn_click)
        self.open_folderButton.clicked.connect(self.open_folder_btn_click)

    def start_btn_click(self):
        print(self.is_start)
        self.is_start = True if not self.is_start else False

        if self.is_start:
            downloader = ThreadDownloader()
            file_manager = FileManager()
            key_manager = KeyManager()

            self.back_ground_task = Core(url=self.url_input.text(), save_file_name=self.file_name.text(),
                                         thread_count=self.thread_count.value(), downloader=downloader,
                                         file_manager=file_manager, key_manager=key_manager)
            # self.back_ground_task.check_fill(file_name_=self.file_name.text(), thread_count_=self.thread_count.value())
            # self.back_ground_task.init(url=self.url_input.text(), file_name=self.file_name.text(),
            #                            thread_count=self.thread_count.value())
            self.back_ground_task.start()
        else:
            self.back_ground_task.stop_flag = True

    def open_folder_btn_click(self):
        path = os.path.join(os.getcwd(), OUT_DIR)
        try:
            # windows
            os.startfile(path)
        except:
            # mac
            os.system("open {}".format(path))


def make_url(host, dir_path, api):
    if api.startswith('http'):
        return api
    elif api.startswith('/'):
        return host + api
    else:
        return host + dir_path + '/' + api


class M3U8File:
    def __init__(self, start_url):
        self.star_url = start_url
        self.host = None
        self.dir_path = None
        self.iv = None
        self.key = None
        self.mode = None
        self.ts_list = []

    def loads(self):
        try:
            split_url = urlparse(self.star_url)
            self.host = '{scheme}://{hostname}{port}'.format(scheme=split_url.scheme, hostname=split_url.hostname,
                                                             port=(':' + str(
                                                                 split_url.port)) if split_url.port else '')
            self.dir_path = '/' + '/'.join(self.star_url.split('//')[-1].split('/')[1:-1])

            text_data = req_url(self.star_url).text
            new_url = re.findall(r'.*?\.m3u8.*', text_data)
            if new_url:
                self.star_url = make_url(self.host, self.dir_path, new_url[0])
                return self.loads()
            else:

                self.find_key(text_data)
                self.find_ts(text_data)
                return True
        except:
            return False

    def find_key(self, text_data):
        data = re.findall(r'#EXT-X-KEY:(.*?)\n', text_data)
        if not data:
            return None
        else:
            data = data[0]
        key_dic_temp = data.split(',')
        key_dic = {}
        for n in key_dic_temp:
            temp_list = n.split('=')
            key_dic[temp_list[0]] = '='.join(temp_list[1:]).strip('"').strip("'")
        self.iv = key_dic.get('IV').replace("0x", "")[:16].encode() if key_dic.get('IV') else None
        self.mode = key_dic.get('METHOD')
        key_url = make_url(self.host, self.dir_path, key_dic.get('URI'))
        self.key = req_url(key_url).content

    def find_ts(self, text_data):
        ts_data = re.findall(r'(?!#).*?\.ts.*', text_data)
        final = []
        for i in ts_data:
            final.append(make_url(self.host, self.dir_path, i))
        self.ts_list = final


class KeyManager:
    def __init__(self):
        self.key = None
        self.iv = None
        self.mode = None
        self.crypter = None
        self.mode_map = {
            'AES-128': AES.MODE_CBC
        }

    def init(self, key_data, iv_data, mode_str):
        if not mode_str:
            return
        self.key = key_data
        self.iv = iv_data
        self.mode = self.mode_map.get(mode_str.upper())
        if self.mode:
            if self.key and self.iv:
                self.crypter = AES.new(self.key, self.mode, self.iv)
            else:
                self.crypter = AES.new(self.key, self.mode)

    def decode(self, data):
        if self.crypter:
            return self.crypter.decrypt(data)
        else:
            return data


class FileManager(QObject):
    _progress_signal = pyqtSignal(int, int)

    def __init__(self):
        super(FileManager, self).__init__()
        self.file_list = []
        self.key_manager = None
        self.out_name = None
        self._progress_signal.connect(set_progress)

    def init(self, out_name, key_manager: KeyManager, ts_list):
        self.file_list = [i.split('/')[-1].replace('.', '').replace('.', '') for i in ts_list]
        self.key_manager = key_manager
        self.out_name = out_name

    def concat(self):
        all_files_count = len(self.file_list)
        now = 0
        with open('{}/{}'.format(OUT_DIR, self.out_name), 'wb+') as f:
            for file_name in self.file_list:
                with open(TEMP_DIR + '/' + file_name, 'rb') as son_data:
                    while True:
                        d = son_data.read(1024)
                        if d:
                            decode_data = self.key_manager.decode(d)
                            f.write(decode_data)
                        else:
                            break
                now += 1
                self._progress_signal.emit(now, all_files_count)
            f.flush()

    def clear(self):
        for cache_file in self.file_list:
            os.remove(TEMP_DIR + '/' + cache_file)
        if os.path.isfile(TEMP_DIR + '/list.txt'):
            os.remove(TEMP_DIR + '/list.txt')


class ThreadDownloader(QThread):
    _signal = pyqtSignal(str)
    _progress_signal = pyqtSignal(int, int)

    def __init__(self):
        super(ThreadDownloader, self).__init__()
        self.task_queue = None
        self.task_count = None
        self.finish_count = 0
        self.task_max_count = None
        self.finish = False
        self._progress_signal.connect(set_progress)
        self._signal.connect(print_log)

    def init(self, task_max_count, task_queue):
        self.task_max_count = task_max_count
        self.task_queue = task_queue

    async def fetch(self, url, headers, session: aiohttp.ClientSession):
        file_name = url.split('/')[-1].replace('.', '')
        if check_file_exist(file_name):
            return
        async with session.get(url, headers=headers, ssl=False) as resp:
            with open(TEMP_DIR + '/' + file_name, 'wb') as fd:
                if resp.status != 200:
                    raise Exception('请求失败')
                while True:
                    chunk = await resp.content.read()
                    if not chunk:
                        break
                    fd.write(chunk)
            self._signal.emit('写入: ' + file_name)

    async def download_task(self, session: aiohttp.ClientSession):
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/61.0.3163.100 Safari/537.36'}
        while not self.finish:
            try:
                url = self.task_queue.get(block=False)
            except Empty:
                await asyncio.sleep(1)
                continue
            except:
                print(traceback.format_exc())
                continue
            print(url, ' 开始下载')
            try:
                task = asyncio.ensure_future(self.fetch(url, headers, session))
                await asyncio.wait_for(task, 10)
                self.finish_count += 1
                self._progress_signal.emit(self.finish_count, self.task_count)
                print(' 成功++++')
                print('{}/{}'.format(self.finish_count, self.task_count))
                if self.finish_count == self.task_count:
                    self.finish = True
            except CancelledError:
                break
            except:
                print(traceback.format_exc())
                self.task_queue.put(url)
                print(' 失败----')

    async def download(self):
        self.task_count = self.task_queue.qsize()
        self.finish = False
        tasks = []
        async with aiohttp.ClientSession() as session:
            loop = asyncio.get_event_loop()
            for i in range(self.task_max_count):
                try:
                    task = loop.create_task(self.download_task(session))
                    tasks.append(task)
                except Empty:
                    await asyncio.sleep(0)
            await asyncio.wait(tasks)
            print('全部停止')

    def run(self):
        asyncio.run(self.download())


class Core(QThread):
    _signal = pyqtSignal(str)
    _start_btn_signal = pyqtSignal(int)
    _set_thread_count_and_filename_signal = pyqtSignal(int, str)
    _finish_signal = pyqtSignal()

    def __init__(self, url, thread_count, save_file_name, downloader: ThreadDownloader, file_manager: FileManager,
                 key_manager: KeyManager):
        super(Core, self).__init__()
        self.url = url
        self.save_file_name = save_file_name
        self.thread_count = thread_count
        self.url_queue = Queue()
        self.downloader = downloader
        self.m3u8_file = None
        self.stop_flag = False
        self.key_manager = key_manager
        self.file_manager = file_manager
        self._signal.connect(print_log)
        self._start_btn_signal.connect(set_start_btn)
        self._set_thread_count_and_filename_signal.connect(set_filename_thread_count)

    def init(self, url, file_name, thread_count):
        self.url = url
        self.save_file_name = file_name
        self.thread_count = thread_count

    def check_fill(self, thread_count_, file_name_):
        try:
            thread_count = int(thread_count_)
        except:
            thread_count = 256
        if 256 < thread_count:
            thread_count = 256
        if thread_count < 1:
            thread_count = 1
        self.save_file_name = make_file_name(file_name_)
        self.thread_count = thread_count
        self._set_thread_count_and_filename_signal.emit(thread_count, self.save_file_name)

    def run(self):
        self._start_btn_signal.emit(2)
        self.check_fill(self.thread_count, self.save_file_name)
        self.m3u8_file = M3U8File(self.url)
        self._signal.emit('开始解析...')
        if self.m3u8_file.loads():
            self._signal.emit('解析成功...')
            for i in self.m3u8_file.ts_list:
                self.url_queue.put(i)
            self.key_manager.init(self.m3u8_file.key, self.m3u8_file.iv, self.m3u8_file.mode)
            self.file_manager.init(out_name=self.save_file_name, key_manager=self.key_manager,
                                   ts_list=self.m3u8_file.ts_list)
            self.downloader.init(self.thread_count, self.url_queue)
            self.downloader.start()
            while not self.downloader.isFinished():
                if self.stop_flag:
                    self._start_btn_signal.emit(0)
                    self.downloader.finish = True
                time.sleep(1)
            if self.stop_flag:
                self._signal.emit('已停止.')
            else:
                self._signal.emit('开始合并.')
                self.file_manager.concat()
                self._signal.emit('清理缓存.')
                self.file_manager.clear()
                self._signal.emit('完成')
            set_finish()
        else:
            self._signal.emit('解析失败...')
            set_finish()
            return


def make_file_name(user_set_name):
    if not user_set_name:
        file_name = str(time.strftime('%Y%m%d%H%M%S', time.localtime())) + '.mp4'
    else:
        file_name = user_set_name
    return file_name


def check_file_exist(file_name):
    if Path(TEMP_DIR + '/' + file_name).is_file():
        return True
    else:
        return False


def ffmpeg_concat(file_name_list, out_name):
    with open(TEMP_DIR + '/list.txt', 'w+', encoding='utf-8') as f:
        for file_name in file_name_list:
            f.write("file '{}'\n".format(file_name))
        f.flush()
    ver = os.popen("ffmpeg -y -f concat -safe 0 -i {}/list.txt -c copy {}/{}".format(TEMP_DIR, OUT_DIR, out_name))
    ver.close()


def print_log(text):
    main_ui.detail_label.setText(text)


def set_progress(finish, all):
    if all == 0:
        return
    main_ui.progressBar.setValue(finish / all * 100)
    main_ui.progress_text.setText('{}/{}'.format(finish, all))


def set_start_btn(status):
    if status == 0:
        main_ui.startButton.setText('停止中...')
        main_ui.startButton.setDisabled(True)
    elif status == 1:
        main_ui.startButton.setText('开始')
        main_ui.startButton.setDisabled(False)
    elif status == 2:
        main_ui.startButton.setText('停止')
        main_ui.progressBar.setValue(0)
        main_ui.startButton.setDisabled(False)


def set_finish():
    main_ui.is_start = False
    main_ui.startButton.setText('开始')
    main_ui.startButton.setEnabled(True)


def set_filename_thread_count(i, s):
    main_ui.thread_count.setValue(i)
    main_ui.file_name.setText(s)


TEMP_DIR = 'm3u8_temp'
OUT_DIR = 'm3u8_output'

if __name__ == '__main__':
    app = QtWidgets.QApplication(sys.argv)

    Form = QtWidgets.QWidget()
    main_ui = MyDownloadUi()
    main_ui.setupUi(Form)
    main_ui.set_action()
    main_ui.url_input.setFocus()
    Form.setWindowTitle('M3U8下载器')

    Form.show()

    sys.exit(app.exec_())
