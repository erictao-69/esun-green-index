# app.py
from __future__ import annotations
import os, io, csv, json
from math import isfinite
from datetime import datetime, date, timedelta
from collections import defaultdict, deque
from flask import Flask, request, jsonify, render_template_string, send_file

app = Flask(__name__)

# ---------------- Domain ----------------
def _nz(x) -> float:
    try:
        v = float(x)
    except Exception:
        return 0.0
    return max(v if isfinite(v) else 0.0, 0.0)

LEVELS = [
    (80, "鑽石級"),
    (60, "白金級"),
    (40, "黃金級"),
    (20, "銀級"),
    (10, "銅級"),
    (0,  "銅級"),
]

def grade_from_gi(gi: float) -> dict:
    # 單一真源；分級+回饋+額外權益
    if gi >= 80:
        return {"level": "鑽石級", "cashback": "0.5%", "loan_cut": "-0.5%",
                "extra_rights": "ESG基金手續費全免、優先審核綠色貸款"}
    if gi >= 60:
        return {"level": "白金級", "cashback": "0.3%", "loan_cut": "-0.3%",
                "extra_rights": "ESG基金手續費5折"}
    if gi >= 40:
        return {"level": "黃金級", "cashback": "0.2%", "loan_cut": "-0.2%",
                "extra_rights": "綠色商品專屬折扣碼"}
    if gi >= 20:
        return {"level": "銀級", "cashback": "0.1%", "loan_cut": "-0.1%",
                "extra_rights": "月度碳足跡報告"}
    if gi >= 10:
        return {"level": "銅級", "cashback": "0%", "loan_cut": "0%",
                "extra_rights": "月度碳足跡報告"}
    return {"level": "銅級", "cashback": "0%", "loan_cut": "0%",
            "extra_rights": "基本碳足跡查詢服務"}

def _next_threshold(gi: float) -> dict:
    # 回傳下一級門檻（含 100 收頂）
    thresholds = [0,10,20,40,60,80,100]
    for t in thresholds:
        if gi < t:
            return {"target": float(t), "delta": round(max(t - gi, 0.0), 2)}
    return {"target": 100.0, "delta": 0.0}

def compute_scores(
    total: float, s1: float, s2: float, s3: float,
    caps: dict|None = None, weights: dict|None = None
) -> dict:
    caps = caps or {"S1": 40.0, "S2": 70.0, "S3": 80.0}
    weights = weights or {"S1": 0.35, "S2": 0.45, "S3": 0.20}

    ws = sum([_nz(weights.get("S1")), _nz(weights.get("S2")), _nz(weights.get("S3"))]) or 1.0
    w1, w2, w3 = _nz(weights.get("S1"))/ws, _nz(weights.get("S2"))/ws, _nz(weights.get("S3"))/ws

    total, s1, s2, s3 = map(_nz, (total, s1, s2, s3))
    # 自動修正：分項超過總額 → 視為其他=0、總額=分項合計（避免負值邏輯錯）
    spent = s1 + s2 + s3
    if spent > total:
        total = spent
    other = max(total - spent, 0.0)
    denom = total if total > 0 else (spent if spent > 0 else 1.0)

    # 金額→原始分數（到 cap 封頂），保持你原設計
    s1_score = min(100.0 * (s1 / 4000.0), caps["S1"])
    s2_score = min(100.0 * (s2 / 7000.0), caps["S2"])
    s3_score = min(100.0 * (s3 / 8000.0), caps["S3"])

    # 標準化 0–100
    s1_norm = (s1_score / caps["S1"]) * 100.0 if caps["S1"] else 0.0
    s2_norm = (s2_score / caps["S2"]) * 100.0 if caps["S2"] else 0.0
    s3_norm = (s3_score / caps["S3"]) * 100.0 if caps["S3"] else 0.0

    gi = (w1 * s1_norm) + (w2 * s2_norm) + (w3 * s3_norm)
    gi = max(0.0, min(gi, 100.0))

    tier = grade_from_gi(gi)
    reward = f"次月現金回饋率 {tier['cashback']}，綠色貸款利率減碼 {tier['loan_cut']}"

    # 下一級差幾分 + 粗略一鍵建議（線性近似：每元對 GI 斜率）
    # 單位金額對 gi 的斜率（在未封頂區間）： d(gi)/d(sx) ≈ wi * (100 / cap) * d(score)/d(sx)
    # d(score)/d(s1)=100/4000、s2=100/7000、s3=100/8000
    slopes = {
        "S1": (w1 * (100.0 / (caps["S1"] or 1.0)) * (100.0 / 4000.0)) if s1_score < caps["S1"] else 0.0,
        "S2": (w2 * (100.0 / (caps["S2"] or 1.0)) * (100.0 / 7000.0)) if s2_score < caps["S2"] else 0.0,
        "S3": (w3 * (100.0 / (caps["S3"] or 1.0)) * (100.0 / 8000.0)) if s3_score < caps["S3"] else 0.0,
    }
    nxt = _next_threshold(gi)
    suggestions = {}
    for k, m in slopes.items():
        if m > 0:
            need_money = nxt["delta"] / m
            suggestions[k] = round(max(need_money, 0.0), 0)
        else:
            suggestions[k] = None  # 已封頂或無斜率

    labels = ["S1 日常綠色", "S2 耐用品減碳", "S3 二手循環", "其他"]
    values = [round(s1,2), round(s2,2), round(s3,2), round(other,2)]
    percents = [round(v/denom*100, 1) for v in values]

    return {
        "inputs": {"total": total, "s1": s1, "s2": s2, "s3": s3},
        "labels": labels, "values": values, "percents": percents,
        "spent": round(spent,2), "other": round(other,2),
        "s_scores": {"S1": round(s1_score,2), "S2": round(s2_score,2), "S3": round(s3_score,2)},
        "s_norms": {"S1": round(s1_norm,2), "S2": round(s2_norm,2), "S3": round(s3_norm,2)},
        "gi": round(gi,2),
        "level": tier["level"],
        "reward": reward,
        "extra_rights": tier["extra_rights"],
        "next_target": nxt,
        "suggestions": suggestions,
        "caps": caps, "weights": {"S1": round(w1,4), "S2": round(w2,4), "S3": round(w3,4)}
    }

# ---- history store (CSV 匯入 -> 月彙總) ----
DATA_DIR = "/mnt/data"
os.makedirs(DATA_DIR, exist_ok=True)
HIST_FILE = os.path.join(DATA_DIR, "receipts.json")

def _load_hist() -> list[dict]:
    if not os.path.exists(HIST_FILE):
        return []
    try:
        with open(HIST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def _save_hist(rows: list[dict]) -> None:
    with open(HIST_FILE, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)

def _parse_date(s: str) -> datetime|None:
    s = (s or "").strip()
    fmts = ["%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d"]
    for fmt in fmts:
        try: return datetime.strptime(s, fmt)
        except Exception: pass
    return None

def aggregate_monthly(receipts: list[dict]) -> list[dict]:
    buck = defaultdict(lambda: {"s1":0.0,"s2":0.0,"s3":0.0,"other":0.0})
    for r in receipts:
        dt = _parse_date(r.get("date",""))
        if not dt: continue
        cat = (r.get("category","") or "").upper()
        amt = _nz(r.get("amount",0))
        if cat not in {"S1","S2","S3","OTHER"} or amt <= 0: continue
        key = {"S1":"s1","S2":"s2","S3":"s3","OTHER":"other"}[cat]
        buck[dt.strftime("%Y-%m")][key] += amt
    series = []
    for mon, s in buck.items():
        total = s["s1"]+s["s2"]+s["s3"]+s["other"]
        met = compute_scores(total, s["s1"], s["s2"], s["s3"])
        series.append({
            "month": mon, **{k:round(v,2) for k,v in s.items()},
            "total": round(total,2),
            "gi": met["gi"],
            "level": met["level"],
        })
    series.sort(key=lambda x: x["month"])
    return series

def rolling_12m(series: list[dict]) -> list[dict]:
    out, win = [], deque()
    sum_gi = 0.0
    for row in series:
        gi = float(row.get("gi",0.0))
        win.append(gi); sum_gi += gi
        if len(win) > 12:
            sum_gi -= win.popleft()
        avg = sum_gi / len(win)
        tier = grade_from_gi(avg)
        out.append({"month": row["month"], "gi12m": round(avg,2), "level12m": tier["level"]})
    return out

# ---------------- Routes ----------------
@app.get("/health")
def health():
    return {"ok": True, "data_path": DATA_DIR}

@app.get("/")
def index():
    return render_template_string(TEMPLATE)

@app.post("/api/compute")
def api_compute():
    data = request.get_json(force=True, silent=True) or {}
    caps = data.get("caps") or None
    weights = data.get("weights") or None
    res = compute_scores(
        data.get("total",0), data.get("s1",0), data.get("s2",0), data.get("s3",0),
        caps=caps, weights=weights
    )
    return jsonify(res)

@app.post("/api/upload_csv")
def api_upload_csv():
    if "file" not in request.files:
        return jsonify({"error":"no file"}), 400
    f = request.files["file"]
    raw = f.read()
    for enc in ("utf-8","cp950","big5"):
        try:
            buf = io.StringIO(raw.decode(enc))
            break
        except Exception:
            continue
    else:
        buf = io.StringIO(raw.decode("utf-8", errors="ignore"))
    reader = csv.DictReader(buf)
    need = {"date","category","amount"}
    header = { (h or "").strip().lower() for h in (reader.fieldnames or [])}
    if not need.issubset(header):
        return jsonify({"error":"CSV 需含欄位: date, category, amount"}), 400
    ok, skipped = 0, 0
    new = []
    for row in reader:
        dt = _parse_date(row.get("date",""))
        cat = (row.get("category","") or "").upper()
        try: amt = float(row.get("amount",""))
        except Exception: amt = -1
        if not dt or cat not in {"S1","S2","S3","OTHER"} or amt <= 0:
            skipped += 1; continue
        new.append({"date": dt.strftime("%Y-%m-%d"), "category": cat, "amount": round(amt,2)})
        ok += 1
    recs = _load_hist(); recs.extend(new); _save_hist(recs)
    series = aggregate_monthly(recs)
    roll = rolling_12m(series) if series else []
    return jsonify({"inserted": ok, "skipped": skipped, "series": series, "rolling": roll})

@app.get("/api/history")
def api_history():
    recs = _load_hist()
    series = aggregate_monthly(recs)
    roll = rolling_12m(series) if series else []
    return jsonify({"count": len(recs), "series": series, "rolling": roll})

@app.get("/api/export_current_csv")
def api_export_current_csv():
    total = _nz(request.args.get("total", 5000))
    s1 = _nz(request.args.get("s1", 1000))
    s2 = _nz(request.args.get("s2", 2000))
    s3 = _nz(request.args.get("s3", 500))
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["field","value"])
    w.writerow(["total", total]); w.writerow(["S1", s1]); w.writerow(["S2", s2]); w.writerow(["S3", s3])
    mem = io.BytesIO(out.getvalue().encode("utf-8")); mem.seek(0)
    return send_file(mem, mimetype="text/csv", as_attachment=True, download_name="current_inputs.csv")

@app.get("/api/export_history_csv")
def api_export_history_csv():
    recs = _load_hist()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["date","category","amount"])
    for r in recs:
        w.writerow([r["date"], r["category"], r["amount"]])
    mem = io.BytesIO(out.getvalue().encode("utf-8")); mem.seek(0)
    return send_file(mem, mimetype="text/csv", as_attachment=True, download_name="history_receipts.csv")

@app.get("/api/export_history_json")
def api_export_history_json():
    recs = _load_hist()
    mem = io.BytesIO(json.dumps(recs, ensure_ascii=False, indent=2).encode("utf-8")); mem.seek(0)
    return send_file(mem, mimetype="application/json", as_attachment=True, download_name="history_receipts.json")

# ---------------- Template ----------------
TEMPLATE = r"""
<!doctype html>
<html lang="zh-Hant" data-bs-theme="light">
<head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>玉山碳計量存摺｜企劃書對齊版</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css"
      rel="stylesheet" onerror="document.documentElement.classList.add('no-bs')">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js" onerror="window._noChart=true"></script>
<style>
  .pill{border-radius:999px}
  .card-soft{border:0;border-radius:1rem;box-shadow:0 6px 24px rgba(0,0,0,.08)}
  .brand-grad{background:linear-gradient(135deg,#0a7f5c,#34a06f)}
  .fallback{border:1px dashed var(--bs-border-color);border-radius:.5rem;padding:1rem;color:#6c757d}
  .kpi{font-weight:800}
  .kpi-badge{font-weight:600;border-radius:.75rem}
  .muted{color:var(--bs-secondary-color);}
  .progress-tier{height:.75rem}
  .click-edit{cursor:pointer;text-decoration:underline dotted}
  @media print{ header, #advModal, #onboard, .no-print { display:none !important; } body{ -webkit-print-color-adjust:exact; print-color-adjust:exact; } }
</style>
</head>
<body>
<header class="border-bottom py-2 no-print">
  <div class="container d-flex justify-content-between align-items-center">
    <div class="d-flex align-items-center gap-2">
      <span class="badge text-bg-success pill px-3 py-2" aria-label="品牌">🌱 玉山碳計量存摺</span>
      <small class="text-secondary d-none d-sm-inline">等級＋權益＋目標倒推＋導覽</small>
    </div>
    <div class="d-flex gap-2">
      <button class="btn btn-outline-secondary btn-sm pill" id="btnTheme" aria-label="切換主題">🌓</button>
      <button class="btn btn-outline-dark btn-sm pill" id="btnPrint">列印報告</button>
      <button class="btn btn-outline-primary btn-sm pill" data-bs-toggle="modal" data-bs-target="#advModal">進階設定</button>
    </div>
  </div>
</header>

<main class="container my-4" id="root">
  <div class="row g-3">
    <!-- 左：輸入/操作 -->
    <div class="col-12 col-lg-4">
      <div class="card card-soft">
        <div class="card-body">
          <div class="d-flex justify-content-between align-items-center">
            <h5 class="card-title mb-0">月度消費輸入</h5>
            <button id="btnTour" class="btn btn-outline-secondary btn-sm pill no-print">導覽</button>
          </div>
          <div class="vstack gap-3 mt-3">
            <div>
              <label class="form-label">總消費（元）</label>
              <input id="total" type="number" class="form-control" min="0" step="100" value="5000" aria-label="總消費">
            </div>
            <div>
              <label class="form-label">S1 日常綠色</label>
              <input id="s1" type="number" class="form-control" min="0" step="50" value="1000" aria-label="S1">
            </div>
            <div>
              <label class="form-label">S2 耐用品減碳</label>
              <input id="s2" type="number" class="form-control" min="0" step="50" value="2000" aria-label="S2">
            </div>
            <div>
              <label class="form-label">S3 二手循環</label>
              <input id="s3" type="number" class="form-control" min="0" step="50" value="500" aria-label="S3">
            </div>
            <div class="d-flex gap-2">
              <button class="btn btn-success flex-fill pill" id="btnCalc">即時計算</button>
              <button class="btn btn-outline-secondary pill" id="btnReset">重置</button>
              <div class="btn-group">
                <button class="btn btn-outline-secondary pill" id="btnUndo" title="復原">↶</button>
                <button class="btn btn-outline-secondary pill" id="btnRedo" title="重做">↷</button>
              </div>
            </div>

            <div class="border rounded p-2 small text-secondary">
              分項合計：<b id="spentNow">—</b> ｜ 其他：<b id="otherNow">—</b>
              <span id="sumWarn" class="text-danger ms-2"></span>
            </div>

            <div class="d-grid gap-2 no-print">
              <button class="btn btn-outline-primary btn-sm pill" id="btnPreset1">情境：均衡</button>
              <button class="btn btn-outline-primary btn-sm pill" id="btnPreset2">情境：通勤族</button>
              <button class="btn btn-outline-primary btn-sm pill" id="btnPreset3">情境：二手派</button>
            </div>

            <div class="card bg-light-subtle p-2">
              <div class="small">目標等級倒推</div>
              <div class="input-group input-group-sm mt-1">
                <label class="input-group-text">目標</label>
                <select id="goalTier" class="form-select">
                  <option value="40">黃金級 (40)</option>
                  <option value="60">白金級 (60)</option>
                  <option value="80">鑽石級 (80)</option>
                  <option value="100">滿分 (100)</option>
                </select>
                <button id="btnBacksolve" class="btn btn-outline-success">倒推分配</button>
              </div>
              <div class="small text-secondary mt-1" id="backsolveHint">以最省錢估算 S1/S2/S3 分配</div>
            </div>

            <div class="d-grid gap-2">
              <a class="btn btn-outline-dark btn-sm pill" id="btnExport" href="#">匯出目前輸入 CSV</a>
              <div class="d-flex gap-2">
                <a class="btn btn-outline-dark btn-sm pill flex-fill" href="/api/export_history_csv">匯出歷史 CSV</a>
                <a class="btn btn-outline-dark btn-sm pill flex-fill" href="/api/export_history_json">匯出歷史 JSON</a>
              </div>
            </div>

            <div class="alert alert-secondary small mt-2 no-print">
              🔒 資料僅存於本機瀏覽器與本服務資料夾，不會外傳。
              <a href="#" id="privacyLink">查看資料保存方式</a>
            </div>
          </div>
        </div>
      </div>

      <!-- 模擬器 -->
      <div class="card card-soft mt-3">
        <div class="card-body">
          <div class="d-flex justify-content-between align-items-center">
            <h6 class="mb-0">情境模擬器（本月額外變動）</h6>
            <span class="badge text-bg-secondary kpi-badge">預測</span>
          </div>
          <div class="row g-2 mt-1">
            <div class="col-4"><label class="form-label small">ΔS1</label><input id="simS1" type="number" class="form-control" value="0"></div>
            <div class="col-4"><label class="form-label small">ΔS2</label><input id="simS2" type="number" class="form-control" value="0"></div>
            <div class="col-4"><label class="form-label small">ΔS3</label><input id="simS3" type="number" class="form-control" value="0"></div>
          </div>
          <div class="d-grid mt-2">
            <button class="btn btn-outline-success pill" id="btnSim">套用模擬（僅前端）</button>
          </div>
          <div class="small muted mt-2">僅改變本月輸入試算，不會寫入歷史。</div>
        </div>
      </div>

      <div class="card card-soft mt-3 no-print">
        <div class="card-body">
          <div class="d-flex justify-content-between align-items-center">
            <h6 class="mb-0">發票匯入（CSV）</h6>
            <a class="btn btn-outline-primary btn-sm pill" href="data:text/csv;charset=utf-8,date,category,amount%0A2025-08-01,S1,380%0A2025-08-08,S2,2200%0A2025-08-15,S3,900%0A2025-08-20,OTHER,600">下載範例</a>
          </div>
          <div class="input-group mt-2">
            <input id="csvFile" type="file" class="form-control" accept=".csv">
            <button id="btnUpload" class="btn btn-success">上傳</button>
          </div>
          <div class="small text-secondary mt-2" id="histInfo">尚無歷史資料</div>
        </div>
      </div>
    </div>

    <!-- 右：KPI/圖表/明細/趨勢 -->
    <div class="col-12 col-lg-8">
      <div class="card card-soft brand-grad text-white">
        <div class="card-body d-flex flex-wrap justify-content-between align-items-center gap-3">
          <div>
            <div class="text-white-50">本月綠色積分 GI（0–100）</div>
            <div class="display-5 kpi" id="giVal">—</div>
            <div class="text-white-50 small">本月等級：<span id="levelVal">—</span> <span id="deltaBadge" class="badge text-bg-light text-dark ms-1"></span></div>
          </div>
          <div class="text-end">
            <div class="text-white-50">金融回饋 / 額外權益</div>
            <div class="fs-6"><span id="rewardVal">—</span></div>
            <div class="small" id="rightsVal">—</div>
            <div class="small" id="rightsCalendar">生效：—</div>
          </div>
          <div class="text-end">
            <div class="text-white-50">12 個月滾動 GI / 等級</div>
            <div class="fs-3 kpi" id="gi12mVal">—</div>
            <div class="small"><span id="level12mVal">—</span></div>
            <div class="small text-white-50" id="projectionHint">—</div>
          </div>
        </div>
      </div>

      <!-- 等級尺 -->
      <div class="card card-soft mt-2">
        <div class="card-body">
          <div class="d-flex justify-content-between align-items-center">
            <h6 class="mb-0">等級尺與門檻</h6>
            <span class="small text-secondary">把分數翻成人話與門檻</span>
          </div>
          <div class="mt-2">
            <div class="progress progress-tier">
              <div id="tierBar" class="progress-bar" role="progressbar" style="width:0%"></div>
            </div>
            <div class="d-flex justify-content-between small mt-1">
              <span>0</span><span>10 銅</span><span>20 銀</span><span>40 金</span><span>60 白金</span><span>80 鑽石</span><span>100</span>
            </div>
            <div class="small mt-2" id="nextHint">—</div>
            <div class="d-flex gap-2 mt-2" id="quickBtns"></div>
          </div>
        </div>
      </div>

      <div class="row g-3 mt-1">
        <div class="col-12 col-xl-6">
          <div class="card card-soft"><div class="card-body">
            <div class="d-flex justify-content-between align-items-center">
              <h6 class="mb-0">各類消費比例（圓餅）</h6>
              <button class="btn btn-outline-secondary btn-sm pill" id="btnPiePng">下載 PNG</button>
            </div>
            <div id="pieWrap" class="mt-2"><canvas id="pie"></canvas></div>
            <div class="small text-secondary mt-2" id="equivHint">—</div>
          </div></div>
        </div>
        <div class="col-12 col-xl-6">
          <div class="card card-soft"><div class="card-body">
            <div class="d-flex justify-content-between align-items-center">
              <h6 class="mb-0">各維度得分（長條）</h6>
              <button class="btn btn-outline-secondary btn-sm pill" id="btnBarPng">下載 PNG</button>
            </div>
            <div id="barWrap" class="mt-2"><canvas id="bar"></canvas></div>
            <div class="small text-secondary mt-2" id="top3">—</div>
          </div></div>
        </div>
      </div>

      <div class="card card-soft mt-3">
        <div class="card-body">
          <h6 class="mb-2">明細 <span class="small text-secondary">（雙擊金額可編輯）</span></h6>
          <div class="table-responsive">
            <table class="table table-sm align-middle">
              <thead><tr><th>分類</th><th class="text-end">金額</th><th class="text-end">占比</th></tr></thead>
              <tbody id="detailBody"></tbody>
            </table>
          </div>
        </div>
      </div>

      <div class="card card-soft mt-3">
        <div class="card-body">
          <div class="d-flex justify-content-between align-items-center">
            <h6 class="mb-2">GI 趨勢</h6>
            <div class="btn-group btn-group-sm no-print">
              <button class="btn btn-outline-secondary active" id="btnTrendMonthly">月度</button>
              <button class="btn btn-outline-secondary" id="btnTrendRolling">12M 滾動</button>
            </div>
          </div>
          <div id="trendWrap"><canvas id="trend"></canvas></div>
          <div class="small text-secondary mt-1" id="trendHint">上傳 CSV 後自動更新</div>
          <div class="small text-secondary mt-1" id="emptyState" style="display:none;">
            步驟：1) 下載範例 → 2) 填寫 → 3) 上傳，即可看到趨勢。
          </div>
        </div>
      </div>
    </div>
  </div>
</main>

<!-- 進階設定 Modal -->
<div class="modal fade" id="advModal" tabindex="-1">
  <div class="modal-dialog modal-dialog-scrollable">
    <div class="modal-content">
      <div class="modal-header"><h6 class="modal-title">進階設定（上限 & 權重）</h6>
        <button class="btn-close" data-bs-dismiss="modal" aria-label="關閉"></button></div>
      <div class="modal-body">
        <div class="alert alert-secondary small">GI = Σ(權重 × 標準化分數)。權重會自動正規化為 1。</div>
        <div class="row g-2">
          <div class="col-6"><label class="form-label">S1 上限</label><input id="capS1" type="number" class="form-control" value="40"></div>
          <div class="col-6"><label class="form-label">S2 上限</label><input id="capS2" type="number" class="form-control" value="70"></div>
          <div class="col-6"><label class="form-label">S3 上限</label><input id="capS3" type="number" class="form-control" value="80"></div>
          <div class="col-6"><label class="form-label">權重 S1</label><input id="wS1" type="number" step="0.01" class="form-control" value="0.35"></div>
          <div class="col-6"><label class="form-label">權重 S2</label><input id="wS2" type="number" step="0.01" class="form-control" value="0.45"></div>
          <div class="col-6"><label class="form-label">權重 S3</label><input id="wS3" type="number" step="0.01" class="form-control" value="0.20"></div>
        </div>
      </div>
      <div class="modal-footer">
        <button id="btnResetAdv" class="btn btn-outline-secondary">恢復預設</button>
        <button class="btn btn-primary" data-bs-dismiss="modal" id="btnApplyAdv">套用</button>
      </div>
    </div>
  </div>
</div>

<!-- 隱私彈窗 -->
<div class="modal fade" id="privacyModal" tabindex="-1">
  <div class="modal-dialog">
    <div class="modal-content">
      <div class="modal-header"><h6 class="modal-title">資料保存方式</h6><button class="btn-close" data-bs-dismiss="modal"></button></div>
      <div class="modal-body small">
        1) 瀏覽器 LocalStorage 保存你的輸入與設定；2) 匯入的發票資料儲存在伺服器的 /mnt/data/receipts.json；3) 不對外傳輸。
      </div>
      <div class="modal-footer"><button class="btn btn-primary" data-bs-dismiss="modal">了解</button></div>
    </div>
  </div>
</div>

<script>
const $ = (s)=>document.querySelector(s);

/* State & LS */
const LS_MAIN="esun:main:v3", LS_ADV="esun:adv:v3", LS_THEME="esun:theme", LS_TOUR="esun:tour:v1";
let adv = { caps:{S1:40,S2:70,S3:80}, weights:{S1:0.35,S2:0.45,S3:0.20} };
let pieChart=null, barChart=null, trendChart=null;
let trendMode="monthly";
let undoStack=[], redoStack=[]; const STACK_MAX=20;

/* Theme & Print */
(function(){ const t=localStorage.getItem(LS_THEME); if(t) document.documentElement.setAttribute("data-bs-theme",t); })();
$("#btnTheme").onclick=()=>{ const cur=document.documentElement.getAttribute("data-bs-theme")||"light"; const nxt=cur==="light"?"dark":"light"; document.documentElement.setAttribute("data-bs-theme",nxt); localStorage.setItem(LS_THEME,nxt); };
$("#btnPrint").onclick=()=>{ window.print(); };

/* Init form from LS */
(function(){
  try{
    const s = JSON.parse(localStorage.getItem(LS_MAIN)||"{}");
    if(typeof s.total==="number"){ ["total","s1","s2","s3"].forEach(k=>{ $("#"+k).value = s[k] ?? $("#"+k).value; }); }
    const a = JSON.parse(localStorage.getItem(LS_ADV)||"{}");
    if(a.caps){ adv.caps = a.caps; } if(a.weights){ adv.weights = a.weights; }
  }catch{}
  $("#capS1").value=adv.caps.S1; $("#capS2").value=adv.caps.S2; $("#capS3").value=adv.caps.S3;
  $("#wS1").value=adv.weights.S1; $("#wS2").value=adv.weights.S2; $("#wS3").value=adv.weights.S3;
})();

/* Helpers */
function getInputs(){ return { total:+$("#total").value||0, s1:+$("#s1").value||0, s2:+$("#s2").value||0, s3:+$("#s3").value||0 }; }
function setInputs(i){ $("#total").value=i.total; $("#s1").value=i.s1; $("#s2").value=i.s2; $("#s3").value=i.s3; }
function saveInputs(){ const i=getInputs(); localStorage.setItem(LS_MAIN, JSON.stringify(i)); }
function saveAdv(){ localStorage.setItem(LS_ADV, JSON.stringify(adv)); }
function fmt(n){ return (+n||0).toLocaleString(); }
function pushUndo(){ undoStack.push(getInputs()); if(undoStack.length>STACK_MAX) undoStack.shift(); redoStack.length=0; }

/* Compute */
async function recompute(){
  const i = getInputs();
  const res = await fetch("/api/compute",{ method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({ ...i, caps: adv.caps, weights: adv.weights })}).then(r=>r.json());
  updateUI(res);
  saveInputs();
  await refreshTrend(res);
}

function updateUI(res){
  // 合計 & 警示
  $("#spentNow").textContent = fmt(res.spent);
  $("#otherNow").textContent = fmt(res.other);
  const over = (res.spent > res.inputs.total);
  $("#sumWarn").textContent = over ? "分項超過總額，已自動調整" : "";

  // KPI
  $("#giVal").textContent = res.gi.toFixed(2);
  $("#levelVal").textContent = res.level;
  $("#rewardVal").textContent = res.reward;
  $("#rightsVal").textContent = res.extra_rights;

  // 等級尺
  $("#tierBar").style.width = `${res.gi}%`;
  const nxt = res.next_target;
  const delta = nxt.delta;
  $("#nextHint").textContent = delta>0 ? `距離 ${nxt.target} 分還差 ${delta.toFixed(2)} 分` : "已達最高門檻";
  // 一鍵建議
  const qb = $("#quickBtns"); qb.innerHTML="";
  const mapName = {S1:"日常綠色", S2:"耐用品減碳", S3:"二手循環"};
  Object.entries(res.suggestions).forEach(([k,v])=>{
    if(v && v>0){
      const btn=document.createElement("button");
      btn.className="btn btn-outline-success btn-sm pill";
      btn.textContent=`${k} ${mapName[k]} +$${fmt(v)} → 達標`;
      btn.onclick=()=>{ pushUndo(); const i=getInputs(); const ns = {S1:"s1",S2:"s2",S3:"s3"}[k]; setInputs({ ...i, total:i.total+v, [ns]: i[ns]+v }); recompute(); };
      qb.appendChild(btn);
    }
  });

  // 權益生效日曆（下月 1~末）
  const today = new Date();
  const nextMonth = new Date(today.getFullYear(), today.getMonth()+1, 1);
  const endNext = new Date(nextMonth.getFullYear(), nextMonth.getMonth()+1, 0);
  $("#rightsCalendar").textContent = `生效：${nextMonth.toISOString().slice(0,10)} ~ ${endNext.toISOString().slice(0,10)}`;

  // 上月對比徽章
  $("#deltaBadge").textContent = ""; $("#deltaBadge").className="badge text-bg-light text-dark ms-1";
  if(window._lastMonthGi!==undefined){
    const diff = res.gi - window._lastMonthGi;
    const sign = diff>0 ? "▲" : (diff<0?"▼":"=");
    const cls = diff>0 ? "text-bg-success" : (diff<0?"text-bg-danger":"text-bg-secondary");
    $("#deltaBadge").textContent = `${sign} ${diff.toFixed(1)}`;
    $("#deltaBadge").className = `badge ${cls} ms-1`;
  }

  // 明細表（可編輯）
  const body = $("#detailBody");
  body.innerHTML = "";
  for (let idx=0; idx<res.labels.length; idx++){
    const tr = document.createElement("tr");
    const isEditable = idx<3; // S1~S3 可編輯
    const valCell = isEditable ? `<span class="click-edit" data-idx="${idx}">${fmt(res.values[idx])}</span>` : fmt(res.values[idx]);
    tr.innerHTML = `<td>${res.labels[idx]}</td>
      <td class="text-end">${valCell}</td>
      <td class="text-end">${res.percents[idx]}%</td>`;
    body.appendChild(tr);
  }
  // 編輯事件
  body.querySelectorAll(".click-edit").forEach(el=>{
    el.addEventListener("dblclick", (e)=>{
      const idx = +e.target.dataset.idx;
      const cur = res.values[idx];
      const input = document.createElement("input");
      input.type="number"; input.value=cur; input.className="form-control form-control-sm text-end";
      e.target.replaceWith(input); input.focus();
      input.addEventListener("keydown",(ke)=>{
        if(ke.key==="Enter"){ applyEdit(idx, +input.value||0); }
        if(ke.key==="Escape"){ recompute(); }
      });
      input.addEventListener("blur", ()=>applyEdit(idx, +input.value||0));
    });
  });

  // 圖表
  buildPie(res); buildBar(res);

  // 等值提示（簡易比喻：僅示意）
  const kmFactor = 0.02; // 假設每 GI 0.02「等效節省」單位（僅做指標，不作承諾）
  $("#equivHint").textContent = `小提醒：再提升 ${delta>0?delta.toFixed(1):0} 分 ≈ 額外 ${ (delta*kmFactor).toFixed(1) } 單位的低碳行為（示意）。`;

  // Top3（以類別）
  const cats = [{k:"S1",v:res.values[0]},{k:"S2",v:res.values[1]},{k:"S3",v:res.values[2]}].sort((a,b)=>b.v-a.v).slice(0,3);
  $("#top3").textContent = `本月貢獻 Top：${cats.map(c=>c.k+":"+fmt(c.v)).join("、")}`;
}

/* Pie & Bar */
function buildPie(res){
  const el = document.getElementById("pie");
  const data = { labels: res.labels.map((l,i)=>`${l} ${res.percents[i]}%`), datasets:[{ data: res.values }] };
  if (window._noChart || !window.Chart){ $("#pieWrap").innerHTML='<div class="fallback">圖表無法載入（改為文字）：'+ data.labels.join(" ｜ ") +'</div>'; return; }
  if (pieChart) pieChart.destroy();
  pieChart = new Chart(el, {
    type: "pie",
    data,
    options:{ responsive:true, plugins:{ legend:{position:"bottom"}, tooltip:{callbacks:{ label:(ctx)=>`${ctx.label}` }} } },
    plugins:[{ id:"labels", afterDatasetsDraw(chart){ const {ctx} = chart; ctx.save(); chart.getDatasetMeta(0).data.forEach((el,i)=>{ const val = res.values[i]; if(val<=0) return; const {x,y} = el.tooltipPosition(); ctx.font = "12px sans-serif"; ctx.fillStyle = getComputedStyle(document.body).getPropertyValue("--bs-body-color")||"#222"; ctx.textAlign="center"; ctx.fillText(val.toLocaleString(), x, y); }); ctx.restore(); } }]
  });
}
function buildBar(res){
  const el = document.getElementById("bar");
  const data = { labels:["S1","S2","S3"], datasets:[{ data:[res.s_scores.S1,res.s_scores.S2,res.s_scores.S3] }] };
  if (window._noChart || !window.Chart){ $("#barWrap").innerHTML='<div class="fallback">圖表無法載入（改為文字）｜S1:'+res.s_scores.S1+' S2:'+res.s_scores.S2+' S3:'+res.s_scores.S3+'</div>'; return; }
  if (barChart) barChart.destroy();
  barChart = new Chart(el,{ type:"bar", data, options:{ responsive:true, scales:{y:{min:0,max:100,ticks:{stepSize:20}}}, plugins:{legend:{display:false}} },
    plugins:[{ id:"labels", afterDatasetsDraw(chart){ const {ctx} = chart; ctx.save(); chart.getDatasetMeta(0).data.forEach((bar,i)=>{ const v=data.datasets[0].data[i]; ctx.fillStyle=getComputedStyle(document.body).getPropertyValue("--bs-body-color")||"#222"; ctx.font="12px sans-serif"; ctx.textAlign="center"; ctx.fillText(v.toFixed(1), bar.x, bar.y-6); }); ctx.restore(); } }]
  });
}

/* Trend */
async function refreshTrend(currentRes){
  const j = await fetch("/api/history").then(r=>r.json());
  const s = j.series||[];
  const roll = j.rolling||[];
  $("#histInfo").textContent = j.count ? `有 ${j.count} 筆發票；月份 ${s.length} 筆` : "尚無歷史資料";
  $("#emptyState").style.display = s.length ? "none":"block";

  // 上月 GI（作為對比）
  if (s.length>=2){ window._lastMonthGi = s[s.length-2].gi; }

  // 12M 現況 KPI
  let gi12m="—", lvl12m="—";
  if (roll.length){
    gi12m = roll[roll.length-1].gi12m.toFixed(2);
    lvl12m = roll[roll.length-1].level12m;
  } else if (currentRes){
    gi12m = currentRes.gi.toFixed(2);
    lvl12m = currentRes.level;
  }
  $("#gi12mVal").textContent = gi12m;
  $("#level12mVal").textContent = lvl12m;
  // 展望：若維持目前模式，12M 將趨近此等級（提示語）
  $("#projectionHint").textContent = gi12m==="—" ? "—" : "若維持現況，12M 等級將趨近 "+lvl12m;

  if (window._noChart || !window.Chart){ $("#trendWrap").innerHTML='<div class="fallback">趨勢圖無法載入</div>'; return; }
  const el = document.getElementById("trend");
  if (trendChart) trendChart.destroy();

  const labelsMonthly = s.map(x=>x.month);
  const dataMonthly = s.map(x=>x.gi);
  const labelsRoll = roll.map(x=>x.month);
  const dataRoll = roll.map(x=>x.gi12m);

  const labels = trendMode==="monthly" ? labelsMonthly : labelsRoll;
  const data = trendMode==="monthly" ? dataMonthly : dataRoll;

  trendChart = new Chart(el, { type:"line",
    data:{ labels, datasets:[{ data, tension:.25, pointRadius:3 }] },
    options:{ scales:{ y:{ min:0,max:100,ticks:{stepSize:20} } }, plugins:{legend:{display:false}} }
  });

  $("#trendHint").textContent = s.length ? `涵蓋：${s[0]?.month ?? "—"} ~ ${s[s.length-1]?.month ?? "—"}；顯示：${trendMode==="monthly"?"月度":"12M 滾動"}` : "上傳 CSV 後自動更新";
}

/* Apply edit from detail table */
function applyEdit(idx, newVal){
  pushUndo();
  const i=getInputs();
  const mapIdx = {0:"s1",1:"s2",2:"s3",3:"other"};
  if(idx<=2){
    const key = mapIdx[idx];
    let s1=i.s1, s2=i.s2, s3=i.s3;
    if(key==="s1") s1=newVal;
    if(key==="s2") s2=newVal;
    if(key==="s3") s3=newVal;
    const spent = Math.max(0, s1+s2+s3);
    const total = Math.max(spent, i.total);
    setInputs({total,s1,s2,s3});
    recompute();
  } else {
    recompute(); // 其他不可直接編（由 total 推出）
  }
}

/* Back-solve 目標（greedy；先投資單位金額 GI 斜率最高的維度，直到達標或封頂） */
async function backsolve(){
  const target = +$("#goalTier").value;
  const base = await fetch("/api/compute",{method:"POST",headers:{"Content-Type":"application/json"}, body: JSON.stringify({...getInputs(), caps:adv.caps, weights:adv.weights})}).then(r=>r.json());
  if(base.gi>=target){ $("#backsolveHint").textContent="已達標，無需額外調整"; return; }
  const caps = adv.caps, weights=adv.weights;
  let s = { s1:base.inputs.s1, s2:base.inputs.s2, s3:base.inputs.s3, total:base.inputs.total };
  let gi = base.gi;
  const step = 100; // 每步增加金額
  for(let guard=0; guard<2000 && gi<target; guard++){
    // 計算當前斜率（若分數已封頂則斜率為 0）
    const cur = await fetch("/api/compute",{method:"POST",headers:{"Content-Type":"application/json"}, body: JSON.stringify({...s, caps, weights})}).then(r=>r.json());
    const slope = cur.suggestions; // 利用 compute 的斜率資訊：None=封頂
    // 選擇可用且最省錢的項（優先建議金額最小者）
    const cand = Object.entries(slope).filter(([k,v])=>v!==null).sort((a,b)=>a[1]-b[1])[0];
    if(!cand) break;
    const key = cand[0]; const field = {S1:"s1",S2:"s2",S3:"s3"}[key];
    s[field] += step; s.total += step;
    const cur2 = await fetch("/api/compute",{method:"POST",headers:{"Content-Type":"application/json"}, body: JSON.stringify({...s, caps, weights})}).then(r=>r.json());
    gi = cur2.gi;
    if(guard%5===0) $("#backsolveHint").textContent=`計算中… GI ${gi.toFixed(1)} / 目標 ${target}`;
  }
  setInputs(s); $("#backsolveHint").textContent=`完成：估計 GI ≈ ${gi.toFixed(1)}，已回填分配`; recompute();
}

/* Events */
$("#btnCalc").onclick=()=>{ pushUndo(); recompute(); };
["total","s1","s2","s3"].forEach(id=> $("#"+id).addEventListener("input", ()=>{ pushUndo(); recompute(); }));
$("#btnReset").onclick=()=>{ pushUndo(); $("#total").value=5000; $("#s1").value=1000; $("#s2").value=2000; $("#s3").value=500; recompute(); };
$("#btnPreset1").onclick=()=>{ pushUndo(); $("#total").value=8000; $("#s1").value=1800; $("#s2").value=2500; $("#s3").value=900; recompute(); };
$("#btnPreset2").onclick=()=>{ pushUndo(); $("#total").value=9000; $("#s1").value=1200; $("#s2").value=3200; $("#s3").value=600; recompute(); };
$("#btnPreset3").onclick=()=>{ pushUndo(); $("#total").value=7000; $("#s1").value=1500; $("#s2").value=1600; $("#s3").value=1400; recompute(); };
$("#btnUpload").onclick=async()=>{ const f = $("#csvFile").files[0]; if(!f){ alert("請選擇 CSV"); return; } const fd=new FormData(); fd.append("file", f);
  const j = await fetch("/api/upload_csv",{method:"POST", body:fd}).then(r=>r.json());
  if(j.error){ alert(j.error); return; }
  refreshTrend();
};
$("#btnExport").onclick=(e)=>{ const i=getInputs(); e.target.href = `/api/export_current_csv?total=${i.total}&s1=${i.s1}&s2=${i.s2}&s3=${i.s3}`; };
$("#btnPiePng").onclick=()=>{ if(pieChart){ const a=document.createElement("a"); a.download="pie.png"; a.href=pieChart.toBase64Image(); a.click(); }};
$("#btnBarPng").onclick=()=>{ if(barChart){ const a=document.createElement("a"); a.download="bar.png"; a.href=barChart.toBase64Image(); a.click(); }};

$("#btnTrendMonthly").onclick=()=>{ trendMode="monthly"; $("#btnTrendMonthly").classList.add("active"); $("#btnTrendRolling").classList.remove("active"); refreshTrend(); };
$("#btnTrendRolling").onclick=()=>{ trendMode="rolling"; $("#btnTrendRolling").classList.add("active"); $("#btnTrendMonthly").classList.remove("active"); refreshTrend(); };

$("#btnApplyAdv").onclick=()=>{ adv.caps={S1:+$("#capS1").value||40,S2:+$("#capS2").value||70,S3:+$("#capS3").value||80};
  adv.weights={S1:+$("#wS1").value||0.35,S2:+$("#wS2").value||0.45,S3:+$("#wS3").value||0.20}; saveAdv(); recompute(); };
$("#btnResetAdv").onclick=()=>{ adv={caps:{S1:40,S2:70,S3:80},weights:{S1:0.35,S2:0.45,S3:0.20}};
  ["capS1","capS2","capS3"].forEach((id,i)=>$("#"+id).value=[40,70,80][i]);
  ["wS1","wS2","wS3"].forEach((id,i)=>$("#"+id).value=[0.35,0.45,0.20][i]); };

$("#btnSim").onclick=async()=>{
  pushUndo();
  const base = getInputs();
  const dx = { s1:+$("#simS1").value||0, s2:+$("#simS2").value||0, s3:+$("#simS3").value||0 };
  const sim = { total: Math.max(0, base.total + dx.s1 + dx.s2 + dx.s3),
                s1: Math.max(0, base.s1 + dx.s1),
                s2: Math.max(0, base.s2 + dx.s2),
                s3: Math.max(0, base.s3 + dx.s3) };
  setInputs(sim); recompute();
};

$("#btnBacksolve").onclick=()=>{ pushUndo(); backsolve(); };

$("#btnUndo").onclick=()=>{ if(!undoStack.length) return; const cur=getInputs(); redoStack.push(cur); const prev=undoStack.pop(); setInputs(prev); recompute(); };
$("#btnRedo").onclick=()=>{ if(!redoStack.length) return; const cur=getInputs(); undoStack.push(cur); const nxt=redoStack.pop(); setInputs(nxt); recompute(); };

/* Tour & Privacy */
$("#btnTour").onclick=runTour;
$("#privacyLink").onclick=(e)=>{ e.preventDefault(); new bootstrap.Modal(document.getElementById('privacyModal')).show(); };

function runTour(){
  alert("快速導覽：1) 輸入金額 2) 看分數與下一級差距 3) 按一鍵建議或倒推目標。");
}

/* First paint */
(async function init(){
  const bs = document.createElement("script"); bs.src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"; document.body.appendChild(bs);
  pushUndo(); await recompute();
})();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    print("[INFO] run on http://127.0.0.1:8000")
    app.run(host="0.0.0.0", port=8000, debug=False)
