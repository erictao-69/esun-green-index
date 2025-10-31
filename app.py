# app.py
from carbon_passbook import app  # 引入 carbon_passbook.py 中的 app

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
