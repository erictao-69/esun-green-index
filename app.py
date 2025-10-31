from flask import Flask

# 初始化 Flask 應用
app = Flask(__name__)

@app.route("/")
def home():
    return "Hello, Render!"  # 替換成你的應用邏輯

# 定義 Gunicorn 配置
if __name__ == "__main__":
    from gunicorn.app.base import BaseApplication
    from gunicorn.six import iteritems

    class FlaskGunicornApplication(BaseApplication):
        def __init__(self, app):
            self.app = app
            super(FlaskGunicornApplication, self).__init__()

        def load(self):
            return self.app

        def load_config(self):
            # 配置 Gunicorn
            for key, value in iteritems(self.app.config):
                self.cfg.set(key.lower(), value)

    # 使用 Gunicorn 啟動應用
    FlaskGunicornApplication(app).run()
