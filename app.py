import os, sys, json, sqlite3, threading, webbrowser
from flask import Flask, request, jsonify, send_from_directory
import swisseph as swe

BASE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
WEB = os.environ.get("JYOTISH_WEB") == "1"   # hosted online: server stores nothing, saved kundalis live in the phone's browser
DATA = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "JyotishApp")
if not WEB:
    os.makedirs(DATA, exist_ok=True)
DB = os.path.join(DATA, "jyotish.db")
app = Flask(__name__, static_folder=os.path.join(BASE, "static"))

def db():
    if WEB:
        raise RuntimeError("server storage disabled in web mode")
    c = sqlite3.connect(DB)
    c.execute("CREATE TABLE IF NOT EXISTS charts(id INTEGER PRIMARY KEY, name TEXT, data TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP)")
    return c

PLANETS = [(swe.SUN, "Su"), (swe.MOON, "Mo"), (swe.MARS, "Ma"), (swe.MERCURY, "Bu"),
           (swe.JUPITER, "Gu"), (swe.VENUS, "Sk"), (swe.SATURN, "Sa"), (swe.MEAN_NODE, "Ra")]

def calc(y, mo, d, h, mi, tz, lat, lon):
    utc_hour = h + mi / 60 - tz
    jd = swe.julday(y, mo, d, utc_hour)
    swe.set_sid_mode(swe.SIDM_LAHIRI)
    ay = swe.get_ayanamsa_ut(jd)
    fl = swe.FLG_MOSEPH | swe.FLG_SIDEREAL | swe.FLG_SPEED
    out = []
    for pid, code in PLANETS:
        xx, _ = swe.calc_ut(jd, pid, fl)
        out.append({"id": code, "lon": xx[0] % 360, "trop": (xx[0] + ay) % 360, "retro": xx[3] < 0 and code != "Ra"})
        if code == "Ra":
            out[-1]["retro"] = True
            k = (xx[0] + 180) % 360
            out.append({"id": "Ke", "lon": k, "trop": (k + ay) % 360, "retro": True})
    _, ascmc = swe.houses_ex(jd, lat, lon, b"W", swe.FLG_SIDEREAL)
    return {"jd": jd, "utc_hour": utc_hour, "ayanamsa": ay, "lagna": ascmc[0] % 360, "mc": ascmc[1] % 360,
            "lagna_trop": (ascmc[0] + ay) % 360, "planets": out}

@app.post("/api/calc")
def api_calc():
    p = request.get_json()
    try:
        return jsonify(calc(int(p["y"]), int(p["mo"]), int(p["d"]), int(p["h"]), int(p["mi"]),
                            float(p["tz"]), float(p["lat"]), float(p["lon"])))
    except Exception as e:
        return jsonify({"error": str(e)}), 400


import datetime

def sid(jd, body):
    swe.set_sid_mode(swe.SIDM_LAHIRI)
    return swe.calc_ut(jd, body, swe.FLG_MOSEPH | swe.FLG_SIDEREAL)[0][0] % 360

def fmt(jd, tz):
    y, m, d, h = swe.revjul(jd + tz / 24)
    dt = datetime.datetime(y, m, d) + datetime.timedelta(minutes=round(h * 60))
    return dt.strftime("%Y-%m-%d %H:%M")

def find_end(fn, unit, jd0):
    v0 = fn(jd0)
    need = unit - (v0 % unit)
    t = jd0
    while ((fn(t) - v0) % 360) < need and t - jd0 < 3:
        t += 0.02
    lo, hi = t - 0.02, t
    for _ in range(30):
        mid = (lo + hi) / 2
        if ((fn(mid) - v0) % 360) < need:
            lo = mid
        else:
            hi = mid
    return hi


def _signed(j):
    return ((sid(j, swe.MOON) - sid(j, swe.SUN)) % 360 + 180) % 360 - 180

def new_moon_near(t):
    lo, hi = t - 2.5, t + 2.5
    for _ in range(40):
        mid = (lo + hi) / 2
        if _signed(mid) < 0:
            lo = mid
        else:
            hi = mid
    return hi

def lunar_info(y, mo, tz, rise, el, sl):
    prev = new_moon_near(rise - el / 12.19)
    nxt = new_moon_near(prev + 29.53)
    r0, r1 = int(sid(prev, swe.SUN) // 30), int(sid(nxt, swe.SUN) // 30)
    m = (r0 + 1) % 12 + 1                      # 1=Chaitra ... 12=Phalguna (amanta; named by the Sun's rashi at the new moon)
    pm = m if el < 180 else m % 12 + 1         # purnimanta name (Krishna paksha belongs to the next month)
    vs = y + 56 if (mo <= 4 and m >= 9) else y + 57
    return {"m": m, "pm": pm, "adhik": r0 == r1, "vs": vs, "shaka": vs - 135, "kali": vs + 3044,
            "ritu": (m - 1) // 2, "ayana": 0 if int(sl // 30) in (9, 10, 11, 0, 1, 2) else 1,
            "nm_prev": fmt(prev, tz), "nm_next": fmt(nxt, tz)}

RAHU_PART = {0: 2, 1: 7, 2: 5, 3: 6, 4: 4, 5: 3, 6: 8}  # python weekday Mon=0

def panchang(y, mo, d, tz, lat, lon):
    jd0 = swe.julday(y, mo, d, 0) - tz / 24
    rise = swe.rise_trans(jd0, swe.SUN, swe.CALC_RISE, (lon, lat, 0), 0, 0, swe.FLG_MOSEPH)[1][0]
    sset = swe.rise_trans(rise, swe.SUN, swe.CALC_SET, (lon, lat, 0), 0, 0, swe.FLG_MOSEPH)[1][0]
    S, M = (lambda j: sid(j, swe.SUN)), (lambda j: sid(j, swe.MOON))
    elong = lambda j: (M(j) - S(j)) % 360
    ysum = lambda j: (M(j) + S(j)) % 360
    sl, ml = S(rise), M(rise)
    el, ys = elong(rise), ysum(rise)
    wd = datetime.date(y, mo, d).weekday()
    seg = (sset - rise) / 8
    rs = rise + (RAHU_PART[wd] - 1) * seg
    return {"sunrise": fmt(rise, tz), "sunset": fmt(sset, tz), "weekday": wd, "sun_lon": sl, "moon_lon": ml,
            "elong": el, "ysum": ys, "jd_rise": rise,
            "tithi": {"n": int(el // 12) + 1, "end": fmt(find_end(elong, 12, rise), tz)},
            "nak": {"n": int(ml // (40 / 3)), "pada": int((ml % (40 / 3)) // (10 / 3)) + 1, "end": fmt(find_end(M, 40 / 3, rise), tz)},
            "yoga": {"n": int(ys // (40 / 3)), "end": fmt(find_end(ysum, 40 / 3, rise), tz)},
            "karan": {"k": int(el // 6), "end": fmt(find_end(elong, 6, rise), tz)},
            "rahu": {"part": RAHU_PART[wd], "s": fmt(rs, tz), "e": fmt(rs + seg, tz)},
            "lunar": lunar_info(y, mo, tz, rise, el, sl)}

@app.post("/api/panchang")
def api_panchang():
    p = request.get_json()
    try:
        return jsonify(panchang(int(p["y"]), int(p["mo"]), int(p["d"]), float(p["tz"]), float(p["lat"]), float(p["lon"])))
    except Exception as e:
        return jsonify({"error": str(e)}), 400


CH_START = {0: 3, 1: 6, 2: 2, 3: 5, 4: 1, 5: 4, 6: 0}   # python weekday Mon=0 -> index in Udveg,Char,Labh,Amrit,Kaal,Shubh,Rog
HORA_ORDER = [0, 5, 3, 1, 6, 4, 2]                      # Sun, Venus, Mercury, Moon, Saturn, Jupiter, Mars
DAY_LORD = {0: 1, 1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 0}   # Mon..Sun -> Moon, Mars, Mercury, Jupiter, Venus, Saturn, Sun
YAMA = {0: 4, 1: 3, 2: 2, 3: 1, 4: 7, 5: 6, 6: 5}
GULI = {0: 6, 1: 5, 2: 4, 3: 3, 4: 2, 5: 1, 6: 7}

def muhurat(y, mo, d, tz, lat, lon):
    jd0 = swe.julday(y, mo, d, 0) - tz / 24
    rise = swe.rise_trans(jd0, swe.SUN, swe.CALC_RISE, (lon, lat, 0), 0, 0, swe.FLG_MOSEPH)[1][0]
    sset = swe.rise_trans(rise, swe.SUN, swe.CALC_SET, (lon, lat, 0), 0, 0, swe.FLG_MOSEPH)[1][0]
    nrise = swe.rise_trans(sset, swe.SUN, swe.CALC_RISE, (lon, lat, 0), 0, 0, swe.FLG_MOSEPH)[1][0]
    wd = datetime.date(y, mo, d).weekday()
    part = lambda a, b, n, i: {"s": fmt(a + (b - a) * i / n, tz), "e": fmt(a + (b - a) * (i + 1) / n, tz)}
    chd, chn = [], []
    for i in range(8):
        chd.append({"n": (CH_START[wd] + i) % 7, **part(rise, sset, 8, i)})
        chn.append({"n": (CH_START[wd] + 5 - 2 * i) % 7, **part(sset, nrise, 8, i)})
    st = HORA_ORDER.index(DAY_LORD[wd])
    hora = [{"p": HORA_ORDER[(st + i) % 7], **(part(rise, sset, 12, i) if i < 12 else part(sset, nrise, 12, i - 12))} for i in range(24)]
    return {"weekday": wd, "dinman": round((sset - rise) * 1440), "ratriman": round((nrise - sset) * 1440),
            "muhurta_len": round((sset - rise) * 1440 / 15, 1),
            "abhijit": part(rise, sset, 15, 7),
            "yama": {"part": YAMA[wd], **part(rise, sset, 8, YAMA[wd] - 1)},
            "guli": {"part": GULI[wd], **part(rise, sset, 8, GULI[wd] - 1)},
            "chd": chd, "chn": chn, "hora": hora}

@app.post("/api/muhurat")
def api_muhurat():
    p = request.get_json()
    try:
        return jsonify(muhurat(int(p["y"]), int(p["mo"]), int(p["d"]), float(p["tz"]), float(p["lat"]), float(p["lon"])))
    except Exception as e:
        return jsonify({"error": str(e)}), 400


def chart_jd(jd, lat, lon):
    swe.set_sid_mode(swe.SIDM_LAHIRI)
    ay = swe.get_ayanamsa_ut(jd)
    fl = swe.FLG_MOSEPH | swe.FLG_SIDEREAL | swe.FLG_SPEED
    out = []
    for pid, code in PLANETS:
        xx, _ = swe.calc_ut(jd, pid, fl)
        out.append({"id": code, "lon": xx[0] % 360, "retro": xx[3] < 0 and code != "Ra"})
        if code == "Ra":
            out[-1]["retro"] = True
            out.append({"id": "Ke", "lon": (xx[0] + 180) % 360, "retro": True})
    _, ascmc = swe.houses_ex(jd, lat, lon, b"W", swe.FLG_SIDEREAL)
    return {"lagna": ascmc[0] % 360, "mc": ascmc[1] % 360, "planets": out}

def varsha(inp, year, lat, lon, tz2):
    y = int(inp["y"])
    nat = calc(y, int(inp["mo"]), int(inp["d"]), int(inp["h"]), int(inp["mi"]), float(inp["tz"]), float(inp["lat"]), float(inp["lon"]))
    target = nat["planets"][0]["lon"]
    if year <= y:
        raise ValueError("Year must be after the birth year")
    swe.set_sid_mode(swe.SIDM_LAHIRI)
    fl = swe.FLG_MOSEPH | swe.FLG_SIDEREAL | swe.FLG_SPEED
    jd = nat["jd"] + (year - y) * 365.256363
    for _ in range(60):
        xx, _ = swe.calc_ut(jd, swe.SUN, fl)
        diff = ((xx[0] - target + 180) % 360) - 180
        jd -= diff / xx[3]
        if abs(diff) < 1e-9:
            break
    xx, _ = swe.calc_ut(jd, swe.SUN, fl)
    ch = chart_jd(jd, lat, lon)
    age = year - y
    return {"jd": jd, "local": fmt(jd, tz2), "age": age, "natal_sun": target,
            "sun_check": xx[0] % 360, "muntha": (int(nat["lagna"] // 30) + age) % 12, **ch}

@app.post("/api/varsha")
def api_varsha():
    p = request.get_json()
    try:
        return jsonify(varsha(p["natal"], int(p["year"]), float(p["lat"]), float(p["lon"]), float(p["tz"])))
    except Exception as e:
        return jsonify({"error": str(e)}), 400


def sun_az_alt(jd, lat, lon):
    xx, _ = swe.calc_ut(jd, swe.SUN, swe.FLG_MOSEPH)
    az, talt, _ = swe.azalt(jd, swe.ECL2HOR, (lon, lat, 0), 0.0, 0.0, (xx[0], xx[1], xx[2]))
    return (az + 180) % 360, talt           # bearing clockwise from north, true altitude

def sun_decl(jd):
    return swe.calc_ut(jd, swe.SUN, swe.FLG_MOSEPH | swe.FLG_EQUATORIAL)[0][1]

def rise_set(y, mo, d, tz, lat, lon):
    jd0 = swe.julday(y, mo, d, 0) - tz / 24
    r = swe.rise_trans(jd0, swe.SUN, swe.CALC_RISE, (lon, lat, 0), 0, 0, swe.FLG_MOSEPH)
    if r[0] != 0:
        raise ValueError("The Sun does not rise or set on this date at this place")
    rise = r[1][0]
    st = swe.rise_trans(rise, swe.SUN, swe.CALC_SET, (lon, lat, 0), 0, 0, swe.FLG_MOSEPH)
    if st[0] != 0:
        raise ValueError("The Sun does not rise or set on this date at this place")
    return rise, st[1][0]

def sunpath(y, mo, d, h, mi, tz, lat, lon):
    rise, sset = rise_set(y, mo, d, tz, lat, lon)
    noon = (rise + sset) / 2
    ra, _ = sun_az_alt(rise, lat, lon)
    sa, _ = sun_az_alt(sset, lat, lon)
    na, nalt = sun_az_alt(noon, lat, lon)
    at_jd = swe.julday(y, mo, d, h + mi / 60 - tz)
    aa, aalt = sun_az_alt(at_jd, lat, lon)
    pts, t = [], rise
    while t <= sset + 1e-9:
        a, al = sun_az_alt(t, lat, lon)
        pts.append({"t": fmt(t, tz)[11:], "az": a, "alt": al})
        t += 1 / 48
    target = round(nalt * 0.7)
    def solve(lo, hi, rising):
        for _ in range(40):
            mid = (lo + hi) / 2
            alt = sun_az_alt(mid, lat, lon)[1]
            if (alt < target) == rising:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2
    j1, j2 = solve(rise, noon, True), solve(noon, sset, False)
    p1, p2 = sun_az_alt(j1, lat, lon), sun_az_alt(j2, lat, lon)
    key = []
    for label, (mm, dd) in (("Mar", (3, 21)), ("Jun", (6, 21)), ("Sep", (9, 23)), ("Dec", (12, 22))):
        r, s2 = rise_set(y, mm, dd, tz, lat, lon)
        key.append({"label": label, "date": "%d-%02d-%02d" % (y, mm, dd), "rise": sun_az_alt(r, lat, lon)[0], "set": sun_az_alt(s2, lat, lon)[0], "decl": sun_decl(r)})
    return {"date": "%d-%02d-%02d" % (y, mo, d), "rise": {"t": fmt(rise, tz)[11:], "az": ra}, "set": {"t": fmt(sset, tz)[11:], "az": sa},
            "noon": {"t": fmt(noon, tz)[11:], "alt": nalt, "az": na}, "decl": sun_decl(noon), "at": {"az": aa, "alt": aalt},
            "pts": pts, "pair": {"alt": target, "t1": fmt(j1, tz)[11:], "az1": p1[0], "t2": fmt(j2, tz)[11:], "az2": p2[0]}, "key": key}

@app.post("/api/sunpath")
def api_sunpath():
    p = request.get_json()
    try:
        return jsonify(sunpath(int(p["y"]), int(p["mo"]), int(p["d"]), int(p["h"]), int(p["mi"]), float(p["tz"]), float(p["lat"]), float(p["lon"])))
    except Exception as e:
        return jsonify({"error": str(e)}), 400


def full_moon_near(t):
    lo, hi = t - 2.5, t + 2.5
    for _ in range(40):
        mid = (lo + hi) / 2
        if ((sid(mid, swe.MOON) - sid(mid, swe.SUN)) % 360) - 180 < 0:
            lo = mid
        else:
            hi = mid
    return hi

ECL_S = [(swe.ECL_TOTAL, "total"), (swe.ECL_ANNULAR_TOTAL, "hybrid"), (swe.ECL_ANNULAR, "annular"), (swe.ECL_PARTIAL, "partial")]
ECL_L = [(swe.ECL_TOTAL, "total"), (swe.ECL_PARTIAL, "partial"), (swe.ECL_PENUMBRAL, "penumbral")]

def yearcal(year, tz, lat, lon):
    jd0 = swe.julday(year, 1, 1, 0) - tz / 24
    jd1 = swe.julday(year + 1, 1, 1, 0) - tz / 24
    S, M = (lambda j: sid(j, swe.SUN)), (lambda j: sid(j, swe.MOON))
    elong = lambda j: (M(j) - S(j)) % 360
    nms = [new_moon_near(jd0 - elong(jd0) / 12.19)]
    while nms[-1] < jd1 + 35:
        nms.append(new_moon_near(nms[-1] + 29.53))
    luns = []
    for i in range(len(nms) - 1):
        r0, r1 = int(S(nms[i]) // 30), int(S(nms[i + 1]) // 30)
        fm = full_moon_near(nms[i] + 14.77)
        if (jd0 <= nms[i] < jd1) or (jd0 <= fm < jd1):
            luns.append({"nm": fmt(nms[i], tz), "full": fmt(fm, tz), "m": (r0 + 1) % 12 + 1, "adhik": r0 == r1,
                         "nak": int(M(fm) // (40 / 3)), "end": fmt(nms[i + 1], tz)})
    sk, t, pr = [], jd0, int(S(jd0) // 30)
    while t < jd1:
        t2 = t + 1
        r = int(S(t2) // 30)
        if r != pr:
            lo, hi = t, t2
            for _ in range(40):
                mid = (lo + hi) / 2
                if int(S(mid) // 30) == pr:
                    lo = mid
                else:
                    hi = mid
            sk.append({"rashi": r, "t": fmt(hi, tz)})
        pr, t = r, t2
    ek, day = [], datetime.date(year, 1, 1)
    for i in range((datetime.date(year + 1, 1, 1) - day).days):
        res, tr = swe.rise_trans(jd0 + i, swe.SUN, swe.CALC_RISE, (lon, lat, 0), 0, 0, swe.FLG_MOSEPH)
        if res != 0:
            continue
        rise = tr[0]
        n = int(elong(rise) // 12) + 1
        if n in (11, 26):
            ek.append({"date": (day + datetime.timedelta(i)).isoformat(), "n": n, "end": fmt(find_end(elong, 12, rise), tz)})
    geo, fl = (lon, lat, 0), swe.FLG_MOSEPH
    sol, t = [], jd0 - 1
    while True:
        r, tr = swe.sol_eclipse_when_glob(t, fl, 0, False)
        if tr[0] >= jd1:
            break
        typ = next(n for b, n in ECL_S if r & b)
        rl, tl, _ = swe.sol_eclipse_when_loc(tr[0] - 2, geo, fl, False)
        sol.append({"t": fmt(tr[0], tz), "type": typ, "visible": abs(tl[0] - tr[0]) < 1})
        t = tr[0] + 20
    lun, t = [], jd0 - 1
    while True:
        r, tr = swe.lun_eclipse_when(t, fl, 0, False)
        if tr[0] >= jd1:
            break
        typ = next(n for b, n in ECL_L if r & b)
        rl, tl, _ = swe.lun_eclipse_when_loc(tr[0] - 2, geo, fl, False)
        lun.append({"t": fmt(tr[0], tz), "type": typ, "visible": abs(tl[0] - tr[0]) < 1})
        t = tr[0] + 20
    return {"year": year, "sankranti": sk, "lunations": luns, "ekadashi": ek, "solar": sol, "lunar": lun}

@app.post("/api/yearcal")
def api_yearcal():
    p = request.get_json()
    try:
        return jsonify(yearcal(int(p["year"]), float(p["tz"]), float(p["lat"]), float(p["lon"])))
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.before_request
def block_server_storage_in_web_mode():
    if WEB and request.path.startswith("/api/charts"):
        return ("Not found", 404)

@app.get("/api/config")
def api_config():
    return jsonify({"storage": "browser" if WEB else "server"})

@app.get("/sw.js")
def sw():
    r = send_from_directory(app.static_folder, "sw.js")
    r.headers["Cache-Control"] = "no-cache"
    return r

@app.get("/manifest.webmanifest")
def manifest():
    return send_from_directory(app.static_folder, "manifest.webmanifest", mimetype="application/manifest+json")

@app.get("/<path:name>")
def static_files(name):
    if name in ("icon.svg", "icon-192.png", "icon-512.png"):
        return send_from_directory(app.static_folder, name)
    return ("Not found", 404)

@app.get("/api/charts")
def charts_list():
    with db() as c:
        rows = c.execute("SELECT id,name,data FROM charts ORDER BY id DESC").fetchall()
    return jsonify([{"id": r[0], "name": r[1], "input": json.loads(r[2])} for r in rows])

@app.post("/api/charts")
def charts_save():
    p = request.get_json()
    with db() as c:
        c.execute("INSERT INTO charts(name,data) VALUES(?,?)", (p["name"], json.dumps(p["input"])))
    return jsonify({"ok": True})

@app.delete("/api/charts/<int:i>")
def charts_del(i):
    with db() as c:
        c.execute("DELETE FROM charts WHERE id=?", (i,))
    return jsonify({"ok": True})

@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")

def free_port(start=5000):
    import socket
    for p in range(start, start + 20):
        with socket.socket() as k:
            if k.connect_ex(("127.0.0.1", p)) != 0:
                return p
    return start

def main():
    try:
        host = os.environ.get("JYOTISH_HOST", "127.0.0.1")
        port = int(os.environ.get("JYOTISH_PORT", 0)) or free_port()
        print("Jyotish App chal raha hai: http://127.0.0.1:%d  (is window ko band mat karo)" % port)
        if host == "0.0.0.0":
            import socket
            try:
                k = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); k.connect(("8.8.8.8", 80)); ip = k.getsockname()[0]; k.close()
                print("Phone se (usi WiFi par) kholo:  http://%s:%d" % (ip, port))
            except Exception:
                print("Phone ke liye apne PC ka IP address (ipconfig) dekho aur http://<IP>:%d kholo" % port)
        threading.Timer(1.5, lambda: webbrowser.open("http://127.0.0.1:%d" % port)).start()
        app.run(host=host, port=port)
    except Exception:
        import traceback
        err = traceback.format_exc()
        open(os.path.join(DATA, "crash.log"), "w").write(err)
        print(err)
        input("Error aaya (crash.log bhi bana hai). Enter dabao...")

if __name__ == "__main__":
    main()
