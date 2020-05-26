# M3U8Downloader
1.M3U8多线程下载器 \
2.用PyQt5做了一个简单的界面

### 使用：
    1.pip3 install -r requirments.txt 
    2.python3 main.py

### 注意：
    1.windows 请将requirments.txt中的pycryptodome替换为pycryptodomex
    2.mac 需要在命令行执行 launchctl limit maxfiles 1024 unlimited 来取消打开多文件限制
    3.pycharm 中最好把 m3u8_output 和 m3u8_temp设置Exclued，
      方法：右键 -> Mark Directory as -> Exclued 
      这样来避免Pycharm一直给这两个文件夹建立索引
    