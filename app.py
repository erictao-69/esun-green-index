# app.py

# 引入 carbon_passbook.py 中的 app
from carbon_passbook import app

if __name__ == "__main__":
    print("[INFO] Starting application...")
    app.run(host="0.0.0.0", port=8000, debug=True)  # 確保啟動 Flask 應用
