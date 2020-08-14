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


def req_url(url, **kwargs):
    print(url)
    try:
        data = requests.get(url=url, timeout=10, verify=False, headers={
            'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/83.0.4103.61 Safari/537.36'},
                            **kwargs)
        if data.status_code != 200:
            raise Exception('{}'.format(data.status_code))
        return data
    except Exception as e:
        print('请求失败， {}, {}'.format(url, e))
        return None


def make_url(host, dir_path, api):
    if api.startswith('http'):
        return api
    elif api.startswith('/'):
        return host + api
    else:
        return host + dir_path + '/' + api


class MyDownloadUi(Ui_Form):
    def __init__(self):
        super(MyDownloadUi, self).__init__()
        self.is_start = False
        self.log_queue = Queue()
        self.back_ground_task = None

    def set_action(self):
        self.startButton.clicked.connect(self.start_btn_click)
        self.open_folderButton.clicked.connect(self.open_folder_btn_click)

    def start_btn_click(self):
        self.is_start = True if not self.is_start else False
        print(self.is_start)

        if self.is_start:
            file_manager = FileManager()

            self.back_ground_task = Core(url=self.url_input.text(), save_file_name=self.file_name.text(),
                                         thread_count=self.thread_count.value(),
                                         file_manager=file_manager)
            self.back_ground_task.check_fill(self.back_ground_task.thread_count, self.back_ground_task.save_file_name,
                                             self.back_ground_task.url)
            self.back_ground_task.downloader = ThreadDownloader(self.back_ground_task.thread_count,
                                                                self.back_ground_task.url_queue)
            self.back_ground_task.start()
        else:
            self.back_ground_task.downloader.stop_flag = True

    def open_folder_btn_click(self):
        path = os.path.join(os.getcwd(), OUT_DIR)
        try:
            # windows
            os.startfile(path)
        except:
            # mac
            os.system("open {}".format(path))


class DownloadItem:
    def __init__(self, url, headers=None):
        self.url = url
        self.headers = {}
        self.file_name = url.split('/')[-1]
        if headers:
            self.headers = headers
            self.file_name = self.file_name.replace('.', '') + '_' + headers.get('Range').split('=')[-1]


class DownloadInfo:
    def __init__(self, start_url):
        self.start_url = start_url
        self.host = None
        self.dir_path = None
        self.iv = None
        self.key = None
        self.mode = None
        self.crypto_ = None
        self.ts_list = []

    def loads(self):
        pass

    def decode(self, data):
        if self.crypto_:
            return self.crypto_.decrypt(data)
        else:
            return data


class SimpleFile(DownloadInfo):
    def __init__(self, start_url, thread_count):
        super(SimpleFile, self).__init__(start_url)
        self.thread_count = thread_count

    def loads(self):
        try:
            response = req_url(self.start_url, stream=True)
            file_size = int(response.headers['content-length'])
            offset = int(file_size / self.thread_count) + 1
            e_size = 0
            while file_size >= e_size:
                s_size = e_size
                e_size = s_size + offset
                self.ts_list.append(
                    DownloadItem(url=self.start_url,
                                 headers={'Range': "bytes=%d-%d" % (s_size, e_size)}))
            return True
        except:
            return False


class M3U8File(DownloadInfo):
    def __init__(self, start_url):
        super(M3U8File, self).__init__(start_url)
        self.key = None
        self.iv = None
        self.mode = None
        self.mode_map = {
            'AES-128': AES.MODE_CBC
        }

    def loads(self):
        try:
            split_url = urlparse(self.start_url)
            self.host = '{scheme}://{hostname}{port}'.format(scheme=split_url.scheme, hostname=split_url.hostname,
                                                             port=(':' + str(
                                                                 split_url.port)) if split_url.port else '')
            self.dir_path = '/' + '/'.join(self.start_url.split('//')[-1].split('/')[1:-1])

            text_data = req_url(self.start_url).text
            new_url = re.findall(r'.*?\.m3u8.*', text_data)
            if new_url:
                self.start_url = make_url(self.host, self.dir_path, new_url[0])
                return self.loads()
            else:
                self.find_key(text_data)
                self.find_ts(text_data)
                if not self.ts_list:
                    return False
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
        self.mode = self.mode_map.get(key_dic.get('METHOD', '').upper())
        key_url = make_url(self.host, self.dir_path, key_dic.get('URI'))
        self.key = req_url(key_url).content
        # init
        if not self.key or not self.mode:
            return
        if self.mode:
            if self.key and self.iv:
                self.crypto_ = AES.new(self.key, self.mode, self.iv)
            else:
                self.crypto_ = AES.new(self.key, self.mode)

    def find_ts(self, text_data):
        ts_data = re.findall(r'(?!#).*?\.ts.*', text_data)
        self.ts_list = []
        for i in ts_data:
            self.ts_list.append(DownloadItem(url=make_url(self.host, self.dir_path, i)))


class FileManager(QObject):
    _progress_signal = pyqtSignal(int, int)

    def __init__(self):
        super(FileManager, self).__init__()
        self.out_name = None
        self.download_file = None
        self._progress_signal.connect(set_progress)

    def init(self, out_name, download_file: DownloadInfo):
        self.download_file = download_file
        self.out_name = out_name

    def concat(self):
        all_files_count = len(self.download_file.ts_list)
        now = 0
        with open('{}/{}'.format(OUT_DIR, self.out_name), 'wb+') as f:
            for file_item in self.download_file.ts_list:
                with open(TEMP_DIR + '/' + file_item.file_name, 'rb') as son_data:
                    while True:
                        d = son_data.read(1024)
                        if d:
                            decode_data = self.download_file.decode(d)
                            f.write(decode_data)
                        else:
                            break
                now += 1
                self._progress_signal.emit(now, all_files_count)
            f.flush()

    def clear(self):
        for cache_file in self.download_file.ts_list:
            FileManager.delete_file(TEMP_DIR + '/' + cache_file.file_name)
        if os.path.isfile(TEMP_DIR + '/list.txt'):
            FileManager.delete_file(TEMP_DIR + '/list.txt')

    @staticmethod
    def delete_file(file_name):
        try:
            os.remove(file_name)
        except FileNotFoundError:
            pass


class ThreadDownloader(QThread):
    _signal = pyqtSignal(str)
    _start_btn_signal = pyqtSignal(int)
    _progress_signal = pyqtSignal(int, int)
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/61.0.3163.100 Safari/537.36'}

    def __init__(self, task_max_count, task_queue):
        super(ThreadDownloader, self).__init__()
        self.task_count = None
        self.finish_count = 0
        self.finish = False
        self.stop_flag = False
        self._progress_signal.connect(set_progress)
        self._signal.connect(print_log)
        self.tasks = []
        self.download_task = None
        self.task_max_count = task_max_count
        self.task_queue = task_queue
        self._start_btn_signal.connect(set_start_btn)

    async def fetch(self, download_item: DownloadItem, session: aiohttp.ClientSession):
        file_name = download_item.file_name
        if check_file_exist(file_name):
            return
        headers_ = self.headers
        async with session.get(download_item.url, headers=headers_.update(download_item.headers),
                               ssl=False) as resp:
            with open(TEMP_DIR + '/' + file_name, 'wb') as fd:
                if resp.status != 200:
                    raise Exception('请求失败')
                else:
                    print('接收资源: {}'.format(download_item.file_name + download_item.headers.get('Range', '')))
                    self._signal.emit(
                        '接收资源: {}'.format(download_item.file_name + download_item.headers.get('Range', '')))
                # block_sz = 2048
                # async for chunk in resp.content.read():
                while 1:
                    chunk = await resp.content.read(8192)
                    if not chunk:
                        break
                    fd.write(chunk)
            self._signal.emit('写入: ' + file_name)

    async def download_async(self, nid, session: aiohttp.ClientSession):
        while not self.finish:
            try:
                url_item = self.task_queue.get(block=False)
            except Empty:
                await asyncio.sleep(1)
                continue
            except:
                print(traceback.format_exc())
                continue
            print(url_item.url, ' 开始下载')
            try:
                await self.fetch(url_item, session)
                self.finish_count += 1
                self._progress_signal.emit(self.finish_count, self.task_count)
                print(' 成功++++')
                print('{}/{}'.format(self.finish_count, self.task_count))
                if self.finish_count == self.task_count:
                    self.finish = True
            except CancelledError:
                print('{} 取消 {}'.format(nid, url_item.url))
                FileManager.delete_file(TEMP_DIR + '/' + url_item.file_name)
                return
            except:
                FileManager.delete_file(TEMP_DIR + '/' + url_item.file_name)
                print(traceback.format_exc())
                self.task_queue.put(url_item)
                print(' 失败----')

    async def stop_listen(self):
        while not self.stop_flag and not self.finish:
            await asyncio.sleep(0)
        if self.stop_flag:
            self._start_btn_signal.emit(0)
            for task in self.tasks:
                task.cancel()

    async def download(self):
        self.task_count = self.task_queue.qsize()
        self.finish = False
        self.tasks = []
        async with aiohttp.ClientSession() as session:
            for i in range(self.task_max_count):
                try:
                    task = asyncio.create_task(self.download_async(i, session))
                    self.tasks.append(task)
                except Empty:
                    await asyncio.sleep(0)
            for task in self.tasks:
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    async def run_async(self):
        self.download_task = asyncio.ensure_future(self.download())
        listen_task = asyncio.ensure_future(self.stop_listen())
        await asyncio.gather(*[listen_task, self.download_task])

    def run(self):
        asyncio.run(self.run_async())
        print('全部停止')


class Core(QThread):
    _signal = pyqtSignal(str)
    _start_btn_signal = pyqtSignal(int)
    _set_thread_count_and_filename_signal = pyqtSignal(int, str)
    _finish_signal = pyqtSignal()

    def __init__(self, url, thread_count, save_file_name, file_manager: FileManager):
        super(Core, self).__init__()
        self.url = url
        self.save_file_name = save_file_name
        self.thread_count = thread_count
        self.url_queue = Queue()
        self.downloader = None
        self.m3u8_file = None
        self.file_manager = file_manager
        self._signal.connect(print_log)
        self._start_btn_signal.connect(set_start_btn)
        self._set_thread_count_and_filename_signal.connect(set_filename_thread_count)

    def init(self, url, file_name, thread_count):
        self.url = url
        self.save_file_name = file_name
        self.thread_count = thread_count

    def check_fill(self, thread_count_, file_name_, url_):
        try:
            thread_count = int(thread_count_)
        except:
            thread_count = 256
        if 256 < thread_count:
            thread_count = 256
        if thread_count < 1:
            thread_count = 1
        self.save_file_name = make_file_name(file_name_, url_)
        self.thread_count = thread_count
        self._set_thread_count_and_filename_signal.emit(thread_count, self.save_file_name)

    def run(self):
        self._start_btn_signal.emit(2)
        self._signal.emit('开始解析...')
        if '.m3u8' in self.url:
            download_info = M3U8File(self.url)
        else:
            download_info = SimpleFile(self.url, self.thread_count)
        if download_info.loads():
            self._signal.emit('解析成功...')
            for i in download_info.ts_list:
                self.url_queue.put(i)
            self.file_manager.init(out_name=self.save_file_name, download_file=download_info)
            self.downloader.start()
            while not self.downloader.isFinished():
                time.sleep(1)
            if self.downloader.stop_flag:
                self._start_btn_signal.emit(1)
                self.downloader.finish = True
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


def make_file_name(user_set_name, url_):
    if not user_set_name:
        if '.m3u8' in url_:
            file_name = str(time.strftime('%Y%m%d%H%M%S', time.localtime()))
        else:
            file_name = url_.split('/')[-1]
        if '.' not in file_name:
            file_name = file_name + '.mp4'
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
