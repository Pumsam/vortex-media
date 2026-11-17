"""
Vortex — локальный веб-загрузчик видео на базе yt-dlp.
Запуск: python app.py   (затем откройте http://127.0.0.1:5000)
"""

import os
import re
import json
import time
import uuid
import ipaddress
import tempfile
import threading
from pathlib import Path

from flask import Flask, render_template, request, jsonify, send_file, after_this_request, Response, redirect
from urllib.parse import quote, urlencode, urlparse
import requests
import yt_dlp

try:
    import imageio_ffmpeg
    FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:  # noqa: BLE001
    FFMPEG_EXE = None

app = Flask(__name__)

# ----------------------------------------------------------------------------
# Настройки
# ----------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "vortex_downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
COOKIES_FILE = BASE_DIR / "cookies.txt"
CONFIG_FILE = BASE_DIR / "config.json"

MAX_AGE_SECONDS = 60 * 30  # удалять временные файлы старше 30 минут


def load_config():
    try:
        if CONFIG_FILE.exists():
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return {}


def save_config(data):
    CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def is_public_url(url):
    """Запрещаем обращаться к внутренним адресам (SSRF)."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:  # noqa: BLE001
        return False
    if not host or host in ("localhost",) or host.endswith(".local"):
        return False
    try:
        ip = ipaddress.ip_address(host)
        return not (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast)
    except ValueError:
        return True


LOGIN_PAGE = """<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Vortex — доступ</title><style>
body{min-height:100vh;display:grid;place-items:center;margin:0;background:#08080f;color:#f4f4fb;
font-family:system-ui,'Segoe UI',sans-serif}
.box{width:min(360px,90vw);padding:32px;border-radius:20px;background:rgba(22,22,38,.75);
border:1px solid rgba(255,255,255,.09);box-shadow:0 24px 70px rgba(0,0,0,.5)}
h1{font-size:22px;margin:0 0 6px}p{color:#9494b0;font-size:13px;margin:0 0 20px}
input{width:100%;box-sizing:border-box;padding:12px;border-radius:10px;background:#11111f;
border:1px solid rgba(255,255,255,.12);color:#fff;outline:none;font-size:14px}
button{width:100%;margin-top:12px;padding:12px;border:none;border-radius:10px;color:#fff;
font-weight:700;font-size:14px;cursor:pointer;background:linear-gradient(120deg,#8b5cf6,#d946ef,#22d3ee)}
</style></head><body><form class="box" method="post">
<h1>Vortex 🔒</h1><p>Введите ключ доступа</p>
<input name="key" type="password" placeholder="Ключ доступа" autofocus>
<button type="submit">Войти</button></form></body></html>"""


@app.before_request
def require_access_key():
    key = (os.environ.get("VORTEX_ACCESS_KEY") or (load_config().get("access_key") or "")).strip()
    if not key:
        return None
    if request.path == "/login":
        if (request.args.get("key") or request.form.get("key") or "") == key:
            resp = redirect("/")
            resp.set_cookie("vortex_key", key, httponly=True, samesite="Lax")
            return resp
        if request.method == "POST":
            return LOGIN_PAGE.replace("</form>", "<p style='color:#fda4af;margin-top:12px'>Неверный ключ</p></form>")
        return LOGIN_PAGE
    if request.cookies.get("vortex_key") == key:
        return None
    return redirect("/login")


def _cleanup_old_files():
    now = time.time()
    for path in DOWNLOAD_DIR.iterdir():
        try:
            if path.is_file() and now - path.stat().st_mtime > MAX_AGE_SECONDS:
                path.unlink(missing_ok=True)
        except OSError:
            pass


def _cleanup_loop():
    while True:
        time.sleep(300)
        _cleanup_old_files()


_cleanup_old_files()
threading.Thread(target=_cleanup_loop, daemon=True).start()


# ----------------------------------------------------------------------------
# Утилиты
# ----------------------------------------------------------------------------
def human_size(num):
    """Байты -> читаемая строка."""
    if not num or num <= 0:
        return None
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(num)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def friendly_error(exc):
    text = str(exc)
    low = text.lower()
    if "unsupported url" in low or "no video formats found" in low:
        return "Сайт не поддерживается или ссылка некорректна."
    if "video unavailable" in low or "private video" in low:
        return "Видео недоступно: удалено, приватное или ограничено."
    if "sign in" in low or "log in" in low or "authentication" in low:
        return "Сайт требует авторизацию. Добавьте cookies.txt в папку программы."
    if "geo" in low or "not available in your country" in low:
        return "Видео недоступно в вашем регионе."
    if "captcha" in low:
        return "Сайт запросил капчу. Попробуйте позже или добавьте cookies.txt."
    if "unable to extract" in low or "extractor error" in low:
        return "Не удалось прочитать страницу. Сайт мог измениться — попробуйте обновить yt-dlp."
    if "http error 404" in low:
        return "Страница не найдена (404). Проверьте ссылку."
    if "ip blocked" in low or "address is blocked" in low or "your ip" in low:
        return "Сайт заблокировал ваш IP-адрес. Попробуйте другую сеть / VPN или подождите."
    if "403" in low or "forbidden" in low:
        return "Доступ запрещён (403). Возможно, сайт блокирует запросы — попробуйте VPN или cookies."
    if "timed out" in low or "connection" in low:
        return "Нет связи с сайтом. Возможно, он заблокирован провайдером — попробуйте VPN/прокси в настройках."
    return text[:300] if text else "Неизвестная ошибка."


def base_opts():
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "retries": 2,
        "socket_timeout": 25,
    }
    # Не переопределяем User-Agent: yt-dlp сам подбирает заголовки и
    # TLS-отпечаток (impersonate), что важно для TikTok/Instagram.
    proxy = (load_config().get("proxy") or "").strip()
    if proxy:
        opts["proxy"] = proxy
    if COOKIES_FILE.exists():
        opts["cookiefile"] = str(COOKIES_FILE)
    return opts


# ----------------------------------------------------------------------------
# Запасной путь для TikTok через публичные зеркала
# (если TikTok блокирует IP напрямую, как это часто бывает)
# ----------------------------------------------------------------------------
MIRROR_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
TIKWM_API = "https://www.tikwm.com/api/"
TIKSY_DOWNLOAD = "https://tiksy.app/api/download"


def is_tiktok_url(url):
    return bool(re.search(r"(^|\.)tiktok\.com($|/)", url or "", re.I))


def tikwm_data(url):
    """Получить метаданные и прямые ссылки на медиа через tikwm."""
    resp = requests.get(
        TIKWM_API,
        params={"url": url},
        headers={"User-Agent": MIRROR_UA, "Accept": "application/json"},
        timeout=40,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("code") != 0 or not payload.get("data"):
        raise RuntimeError(payload.get("msg") or "Зеркало не вернуло данные")
    return payload["data"]


def safe_filename(title, fallback="tiktok"):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", (title or "").strip())[:80]
    return name or fallback


def mirror_formats(data):
    formats = []
    if data.get("play"):
        formats.append({
            "id": "mirror_hd", "ext": "mp4", "quality": "Без водяного знака", "height": 0,
            "fps": None, "size": data.get("size"), "size_text": human_size(data.get("size")),
            "has_video": True, "has_audio": True, "type_label": "Видео + Аудио",
            "note": "Оригинал через зеркало (без водяного знака)",
        })
    if data.get("wmplay"):
        formats.append({
            "id": "mirror_wm", "ext": "mp4", "quality": "С водяным знаком", "height": 0,
            "fps": None, "size": data.get("wm_size"), "size_text": human_size(data.get("wm_size")),
            "has_video": True, "has_audio": True, "type_label": "Видео + Аудио",
            "note": "Версия с водяным знаком TikTok",
        })
    if data.get("music"):
        formats.append({
            "id": "mirror_audio", "ext": "mp3", "quality": "Музыка", "height": 0,
            "fps": None, "size": None, "size_text": None,
            "has_video": False, "has_audio": True, "type_label": "Только аудио",
            "note": "Звук из ролика (MP3)",
        })
    return formats


def probe_tiktok_mirror(url):
    data = tikwm_data(url)
    formats = mirror_formats(data)
    if not formats:
        raise RuntimeError("Зеркало не нашло видео по этой ссылке")

    author = data.get("author") or {}
    uploader = author.get("nickname") or author.get("uniqueId") or None
    created = data.get("create_time")
    date_text = None
    if created:
        try:
            date_text = time.strftime("%d.%m.%Y", time.localtime(int(created)))
        except (ValueError, OSError, TypeError):
            pass

    views = data.get("play_count")
    # Превью тоже проксируем: CDN TikTok недоступен напрямую
    cover = data.get("cover")
    thumbnail = None
    if cover:
        thumbnail = TIKSY_DOWNLOAD + "?" + urlencode({
            "url": cover, "filename": "cover.jpg", "kind": "image",
        })

    return {
        "title": data.get("title") or "TikTok видео",
        "thumbnail": thumbnail,
        "duration": int(data.get("duration")) if data.get("duration") else None,
        "uploader": uploader,
        "platform": "TikTok (зеркало)",
        "views": f"{views:,}".replace(",", " ") if isinstance(views, int) else None,
        "upload_date": date_text,
        "description": "",
        "web_url": url,
        "formats": formats,
        "recommended": "mirror_hd",
        "format_count": len(formats),
    }


def download_tiktok_mirror(url, format_id):
    key_map = {"mirror_hd": "play", "mirror_wm": "wmplay", "mirror_audio": "music"}
    media_key = key_map.get(format_id)
    if not media_key:
        return jsonify(error="Неизвестный формат зеркала."), 400

    data = tikwm_data(url)
    media_url = data.get(media_key)
    if not media_url:
        return jsonify(error="Зеркало не вернуло ссылку на файл."), 400

    is_audio = media_key == "music"
    ext = "mp3" if is_audio else "mp4"
    filename = safe_filename(data.get("title"), "tiktok") + "." + ext
    proxy_url = TIKSY_DOWNLOAD + "?" + urlencode({
        "url": media_url, "filename": filename, "kind": "audio" if is_audio else "video",
    })

    upstream = requests.get(proxy_url, headers={"User-Agent": MIRROR_UA}, stream=True, timeout=(20, 180))
    if upstream.status_code != 200:
        body = upstream.text[:200]
        upstream.close()
        return jsonify(error=f"Зеркало не смогло отдать файл ({upstream.status_code}). {body}"), 502

    total = upstream.headers.get("Content-Length")
    if not total and not is_audio:
        total = data.get("size")

    def generate():
        try:
            for chunk in upstream.iter_content(chunk_size=64 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    headers = {
        "Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename),
    }
    if total:
        headers["Content-Length"] = str(total)
    return Response(
        generate(),
        mimetype="audio/mpeg" if is_audio else "video/mp4",
        headers=headers,
    )


def build_format_entry(fmt, info):
    vcodec = fmt.get("vcodec")
    acodec = fmt.get("acodec")
    has_video = bool(vcodec) and vcodec != "none"
    has_audio = bool(acodec) and acodec != "none"

    protocol = fmt.get("protocol") or ""
    if "mhtml" in protocol or "storyboard" in (fmt.get("format_note") or "").lower():
        return None

    size = fmt.get("filesize") or fmt.get("filesize_approx")
    if not size:
        duration = info.get("duration") or 0
        if fmt.get("tbr"):
            size = (fmt["tbr"] * 1000 / 8) * duration
        elif fmt.get("abr"):
            size = (fmt["abr"] * 1000 / 8) * duration

    height = fmt.get("height")
    width = fmt.get("width")
    if height and width:
        quality = f"{width}×{height}"
    elif height:
        quality = f"{height}p"
    elif fmt.get("abr"):
        quality = f"{int(fmt['abr'])} kbps"
    else:
        quality = fmt.get("format_note") or fmt.get("ext") or "—"

    label_parts = []
    if has_video:
        label_parts.append("Видео")
    if has_audio:
        label_parts.append("Аудио")
    if not label_parts:
        label_parts.append("Файл")

    return {
        "id": str(fmt.get("format_id") or ""),
        "ext": (fmt.get("ext") or "mp4").lower(),
        "quality": quality,
        "height": int(height) if height else 0,
        "fps": fmt.get("fps"),
        "size": int(size) if size and size > 0 else None,
        "size_text": human_size(size),
        "has_video": has_video,
        "has_audio": has_audio,
        "type_label": " + ".join(label_parts),
        "note": fmt.get("format_note") or "",
    }


# ----------------------------------------------------------------------------
# Маршруты
# ----------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/probe", methods=["POST"])
def probe():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify(error="Вставьте ссылку на видео."), 400
    if not re.match(r"^https?://", url, re.I):
        return jsonify(error="Ссылка должна начинаться с http:// или https://"), 400
    if not is_public_url(url):
        return jsonify(error="Разрешены только публичные ссылки (внутренние адреса запрещены)."), 400

    # Для TikTok сначала пробуем зеркало: прямое соединение часто блокируется,
    # а ожидание таймаута yt-dlp занимает ~25 секунд.
    mirror_error = None
    if is_tiktok_url(url):
        try:
            return jsonify(probe_tiktok_mirror(url))
        except Exception as exc:  # noqa: BLE001
            mirror_error = exc

    opts = base_opts()
    opts["skip_download"] = True
    info = None
    yt_error = None
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:  # noqa: BLE001
        yt_error = exc

    if mirror_error and not info:
        return jsonify(error=(
            "Зеркало не сработало: " + str(mirror_error)[:120] +
            (" | " + friendly_error(yt_error) if yt_error else "")
        )), 400

    if yt_error:
        return jsonify(error=friendly_error(yt_error)), 400
    if not info:
        return jsonify(error="Не удалось получить информацию о видео."), 400

    formats = []
    seen = set()
    for fmt in info.get("formats") or []:
        entry = build_format_entry(fmt, info)
        if not entry or not entry["id"]:
            continue
        key = (entry["quality"], entry["ext"], entry["has_video"], entry["has_audio"], entry["size"])
        if key in seen:
            continue
        seen.add(key)
        formats.append(entry)

    formats.sort(key=lambda f: (f["has_video"] and f["has_audio"], f["height"], f["size"] or 0), reverse=True)

    # Лучшие потоки ищем до ограничения списка, чтобы не потерять аудио
    best_video = max((f for f in formats if f["has_video"]), key=lambda f: (f["height"], f["size"] or 0), default=None)
    best_audio = max((f for f in formats if f["has_audio"] and not f["has_video"]),
                     key=lambda f: (f["size"] or 0), default=None)

    video_formats = [f for f in formats if f["has_video"]][:24]
    audio_formats = [f for f in formats if f["has_audio"] and not f["has_video"]][:6]
    formats = video_formats + audio_formats
    if best_video and FFMPEG_EXE:
        combined_size = (best_video["size"] or 0) + (best_audio["size"] or 0) if best_audio else best_video["size"]
        best_entry = {
            "id": "best",
            "ext": "mp4",
            "quality": best_video["quality"],
            "height": best_video["height"],
            "fps": best_video["fps"],
            "size": combined_size or None,
            "size_text": human_size(combined_size) if combined_size else None,
            "has_video": True,
            "has_audio": True,
            "type_label": "Видео + Аудио",
            "note": "Лучшее доступное качество (объединение потоков)",
        }
        formats.insert(0, best_entry)

    recommended = "best" if FFMPEG_EXE and best_video else (formats[0]["id"] if formats else None)

    duration = info.get("duration")
    upload_date = info.get("upload_date")
    date_text = None
    if upload_date and len(upload_date) == 8:
        date_text = f"{upload_date[6:8]}.{upload_date[4:6]}.{upload_date[:4]}"

    views = info.get("view_count")
    views_text = f"{views:,}".replace(",", " ") if isinstance(views, int) else None

    return jsonify(
        title=info.get("title") or "Без названия",
        thumbnail=info.get("thumbnail"),
        duration=int(duration) if duration else None,
        uploader=info.get("uploader") or info.get("channel") or None,
        platform=(info.get("extractor_key") or info.get("extractor") or "Сайт").replace("Extractor", "").strip(),
        views=views_text,
        upload_date=date_text,
        description=(info.get("description") or "")[:300],
        web_url=info.get("webpage_url") or url,
        formats=formats,
        recommended=recommended,
        format_count=len(info.get("formats") or []),
    )


@app.route("/api/download")
def download():
    url = (request.args.get("url") or "").strip()
    format_id = (request.args.get("format") or "").strip()
    if not url or not format_id:
        return jsonify(error="Не хватает параметров для скачивания."), 400
    if not is_public_url(url):
        return jsonify(error="Разрешены только публичные ссылки (внутренние адреса запрещены)."), 400

    if format_id.startswith("mirror_"):
        return download_tiktok_mirror(url, format_id)

    token = uuid.uuid4().hex
    effective_format = "bv*+ba/b" if format_id == "best" else format_id
    opts = base_opts()
    opts.update(
        {
            "outtmpl": str(DOWNLOAD_DIR / f"{token}.%(ext)s"),
            "format": effective_format,
            "overwrites": True,
            "noprogress": True,
        }
    )
    if FFMPEG_EXE:
        opts["ffmpeg_location"] = FFMPEG_EXE
        opts["merge_output_format"] = "mp4"

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filepath = ydl.prepare_filename(info)
    except Exception as exc:  # noqa: BLE001
        return jsonify(error=friendly_error(exc)), 400

    # После объединения потоков итоговый файл может отличаться от prepare_filename
    candidates = sorted(DOWNLOAD_DIR.glob(f"{token}.*"), key=lambda p: p.stat().st_size, reverse=True)
    if candidates:
        filepath = str(candidates[0])
    elif not os.path.exists(filepath):
        return jsonify(error="Файл не был создан. Попробуйте другой формат."), 500

    title = info.get("title") or "video"
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip()[:110] or "video"
    ext = os.path.splitext(filepath)[1] or ".mp4"
    download_name = f"{safe}{ext}"

    @after_this_request
    def _schedule_delete(response):
        def remove_later():
            time.sleep(5)
            try:
                os.remove(filepath)
            except OSError:
                pass

        threading.Thread(target=remove_later, daemon=True).start()
        return response

    return send_file(
        filepath,
        as_attachment=True,
        download_name=download_name,
        conditional=False,
    )


@app.route("/api/config")
def get_config():
    cfg = load_config()
    return jsonify(proxy=cfg.get("proxy", ""), cookies=COOKIES_FILE.exists())


@app.route("/api/config", methods=["POST"])
def set_config():
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    cfg["proxy"] = (data.get("proxy") or "").strip()
    save_config(cfg)
    return jsonify(ok=True, proxy=cfg["proxy"])


@app.route("/api/health")
def health():
    return jsonify(ok=True, ytdlp=yt_dlp.version.__version__, ffmpeg=bool(FFMPEG_EXE))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    # В облаке (HF Spaces / Docker) слушаем все интерфейсы, локально — только localhost
    in_cloud = bool(os.environ.get("PORT") or os.environ.get("HF_SPACE") or os.environ.get("SPACE_ID"))
    host = "0.0.0.0" if in_cloud else "127.0.0.1"
    print("=" * 56)
    print("  VORTEX — Media Downloader")
    print(f"  Адрес: http://{host}:{port}")
    print("  Для остановки нажмите Ctrl+C")
    print("=" * 56)
    app.run(host=host, port=port, debug=False, threaded=True)
