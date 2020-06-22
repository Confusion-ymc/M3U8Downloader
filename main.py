import ctypes
import inspect
import os
import sys
from queue import Queue
import time
import threading

from urllib.parse import urlparse
try:
    from Crypto.Cipher import AES
except ImportError:
    from Cryptodome.Cipher import AES
import requests
from PyQt5 import QtWidgets
from PyQt5.QtCore import pyqtSignal, QObject, QThread
from urllib3 import disable_warnings

from ui import Ui_Form

disable_warnings()

ALL_TASK_COUNT = 0
ALL_FINISH_COUNT = 0


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


class FileController(QObject):
    _signal = pyqtSignal(str)
    _finish_signal = pyqtSignal()

    def __init__(self):
        super(FileController, self).__init__()
        self.all_task = Queue()
        self.final_file_name_list = []
        self.key = None
        self._signal.connect(print_log)
        self._finish_signal.connect(set_finish)

    def find_ts(self, start_url):
        global ALL_TASK_COUNT
        self._signal.emit('开始解析m3u8文件...')
        ts_list = []
        split_url = urlparse(start_url)
        host = '{scheme}://{hostname}{port}'.format(scheme=split_url.scheme, hostname=split_url.hostname,
                                                    port=(':' + str(split_url.port)) if split_url.port else '')
        m3u8_path = '/' + start_url.split('/')[-1]
        dir_path = start_url[len(host):][:-len(m3u8_path)]

        key = None
        data = None
        while not data:
            data = req_url(host + dir_path + m3u8_path)
            if not data:
                self._signal.emit('解析失败，重试...')
                time.sleep(1)
                continue
            data_list = data.text.split('\n')

            for i in data_list:
                if i.startswith('#EXT-X-KEY:'):
                    self._signal.emit('发现密钥')
                    i = i.lstrip('#EXT-X-KEY:')
                    key_dic_temp = i.split(',')
                    key_dic = {}
                    for n in key_dic_temp:
                        k, v = n.split('=')
                        key_dic[k] = v
                    key_path = key_dic.get('URI').strip('"').strip("'")
                    if key_path.startswith('/'):
                        key_url = host + key_path
                    else:
                        key_url = host + dir_path + key_path
                    self._signal.emit('加密方式: {}, 请求密钥...'.format(key_dic.get('METHOD')))
                    key = req_url(key_url).text
                    self._signal.emit('获取密钥成功： {}'.format(key))

                if i.startswith('#'):
                    continue
                if i.endswith('.m3u8'):
                    m3u8_path = '/' + i.split('/')[-1]
                    if i.startswith('/'):
                        dir_path = i[:-len(m3u8_path)]
                    else:
                        dir_path = (dir_path + '/' + i)[:-len(m3u8_path)]
                    data = None
                    break
                elif not i.endswith('.ts'):
                    continue
                else:
                    if i.startswith('/'):
                        ts_list.append(host + i)
                    else:
                        ts_list.append(host + dir_path + '/' + i)
            else:
                self._signal.emit('解析成功！')
                self.key = key
                self.final_file_name_list = [i.split('/')[-1] for i in ts_list]
                ALL_TASK_COUNT = len(self.final_file_name_list)
                # 加入任务
                [self.all_task.put(i) for i in ts_list]
                self._signal.emit('开始下载...')

    def sort_out_file(self):
        self._signal.emit('开始合成...')
        # s = time.time()
        # 使用ffmpeg合并
        # ffmpeg_concat(self.final_file_name_list)
        # print('ffmpeg use {}'.format(time.time() - s))
        s = time.time()
        # 使用python合并
        my_concat(self.final_file_name_list)
        print('my contact use {}'.format(time.time() - s))

    def clear_cache(self):
        self._signal.emit('开始清理缓存...')
        for cache_file in self.final_file_name_list:
            os.remove(TEMP_DIR + '/' + cache_file)
        if os.path.isfile(TEMP_DIR + '/list.txt'):
            os.remove(TEMP_DIR + '/list.txt')


class QDownloadThreadStart(QThread):
    def __init__(self, file_controller: FileController, start_url, downloader_list):
        super(QDownloadThreadStart, self).__init__()

        self.file_controller = file_controller
        self.start_url = start_url
        self.downloader_list = downloader_list

    def run(self):
        global ALL_FINISH_COUNT, ALL_TASK_COUNT
        ALL_FINISH_COUNT = 0
        ALL_TASK_COUNT = 0
        if not os.path.isdir(TEMP_DIR):
            os.makedirs(TEMP_DIR)
        if not os.path.isdir(OUT_DIR):
            os.makedirs(OUT_DIR)
        self.file_controller.find_ts(self.start_url.strip())
        if self.file_controller.key:
            cryptor = AES.new(self.file_controller.key.encode(), AES.MODE_CBC) if self.file_controller.key else None
        else:
            cryptor = None
        for i in self.downloader_list:
            i.set_files(self.file_controller, cryptor)
            i.start()
        for i in self.downloader_list:
            i.wait()
        if ALL_TASK_COUNT == 0:
            print('user exit!')
            self.file_controller._finish_signal.emit()
            return
        self.file_controller.sort_out_file()
        self.file_controller.clear_cache()
        self.file_controller._signal.emit('完成...')
        self.file_controller._finish_signal.emit()
        print('finish')


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
        global ALL_TASK_COUNT
        print(self.is_start)
        self.is_start = True if not self.is_start else False
        try:
            thread_count = int(self.thread_count.text())
        except:
            thread_count = 1
        if 256 < thread_count:
            thread_count = 256
        if 1 > thread_count:
            thread_count = 1
        self.thread_count.setValue(thread_count)

        if self.is_start:
            self.progressBar.setValue(0)
            self.startButton.setText('取消')
            lock = threading.Lock()
            downloader_list = []
            for i in range(thread_count):
                downloader = DownLoader(lock, i + 1)
                downloader_list.append(downloader)
            file_controller = FileController()

            self.back_ground_task = QDownloadThreadStart(file_controller, self.url_input.text(), downloader_list)
            self.back_ground_task.start()
        else:
            ALL_TASK_COUNT = 0
            self.startButton.setText('停止中')
            self.startButton.setDisabled(True)
            self.back_ground_task = None

    def open_folder_btn_click(self):
        path = os.path.join(os.getcwd(), OUT_DIR)
        try:
            # windows
            os.startfile(path)
        except:
            # mac
            os.system("open {}".format(path))


class DownLoader(QThread):
    _signal = pyqtSignal(str)
    _progress_signal = pyqtSignal(int, int)

    def __init__(self, lock, id_int=1):
        super(DownLoader, self).__init__()
        self.id_int = id_int
        self.daemon = True
        self.files = None
        self.lock = lock
        self.cryptor = None
        self._progress_signal.connect(set_progress)
        self._signal.connect(print_log)

    def set_files(self, files, cryptor):
        self.files = files
        self.cryptor = cryptor

    def download(self, url):
        global ALL_FINISH_COUNT
        file_name = url.split('/')[-1]
        with self.lock:
            if os.path.isfile(TEMP_DIR + '/' + file_name):
                ALL_FINISH_COUNT += 1
                return
        if ALL_TASK_COUNT == 0:
            return
        print('开始下载:{}'.format(url))
        data = req_url(url)
        if not data:
            self.files.all_task.put(url)
        elif ALL_TASK_COUNT == 0:
            return
        else:
            with self.lock:
                with open(TEMP_DIR + '/' + file_name, 'wb+') as f:
                    data = data.content
                    if self.cryptor:
                        f.write(self.cryptor.decrypt(data))
                    else:
                        f.write(data)
                    f.flush()
                    ALL_FINISH_COUNT += 1
                print('下载成功:{}'.format(url))
                self._signal.emit('写入: ' + file_name)
                self._progress_signal.emit(ALL_FINISH_COUNT, ALL_TASK_COUNT)

    def run(self):
        while ALL_TASK_COUNT and ALL_FINISH_COUNT != ALL_TASK_COUNT:
            if not self.files.all_task.empty():
                try:
                    task_url = self.files.all_task.get(block=False)
                except:
                    continue
            else:
                return
            self.download(task_url)
        print('thread {} exit'.format(self.id_int))


def my_concat(file_name_list, out_name='out'):
    with open('{}/{}.mp4'.format(OUT_DIR, out_name + str(time.strftime('%Y-%m-%d-%H-%M-%S', time.localtime()))),
              'wb+') as f:
        for file_name in file_name_list:
            with open(TEMP_DIR + '/' + file_name, 'rb') as son_data:
                while True:
                    d = son_data.read(1024)
                    if d:
                        f.write(d)
                    else:
                        break
        f.flush()


def ffmpeg_concat(file_name_list, out_name='out'):
    with open(TEMP_DIR + '/list.txt', 'w+', encoding='utf-8') as f:
        for file_name in file_name_list:
            f.write("file '{}'\n".format(file_name))
        f.flush()
    ver = os.popen("ffmpeg -y -f concat -safe 0 -i {}/list.txt -c copy {}/{}.mp4".format(TEMP_DIR, OUT_DIR, out_name))
    ver.close()


def print_log(text):
    main_ui.detail_label.setText(text)


def set_progress(finish, all):
    if all == 0:
        return
    main_ui.progressBar.setValue(finish / all * 100)
    main_ui.progress_text.setText('{}/{}'.format(finish, all))


def set_finish():
    main_ui.is_start = False
    main_ui.startButton.setText('开始')
    main_ui.startButton.setEnabled(True)


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
