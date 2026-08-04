from flask import Flask, request
import requests

app = Flask(__name__)
# 后端真实服务地址
BACKEND_URL = "http://127.0.0.1:8080"

@app.route("/feishu/callback", methods=["POST"])
def proxy():
    headers = dict(request.headers)
    # 关键：添加绕过ngrok拦截的header
    headers["ngrok-skip-browser-warning"] = "true"
    resp = requests.request(
        method=request.method,
        url=f"{BACKEND_URL}{request.path}",
        headers=headers,
        data=request.get_data(),
        params=request.args
    )
    return resp.content, resp.status_code, resp.headers.items()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8081, debug=False)
