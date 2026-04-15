import logging
import os
import requests
import time
import ssl
import urllib3
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from requests.adapters import HTTPAdapter
from bs4 import BeautifulSoup
from HdRezkaApi import HdRezkaApi, HdRezkaSession

# Отключаем ворнинги SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Глобальный патч для SSL
try:
    ssl._create_default_https_context = ssl._create_unverified_context
except:
    pass

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Адаптер для обхода проверок SSL
class SSLAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        kwargs["cert_reqs"] = ssl.CERT_NONE
        kwargs["assert_hostname"] = False
        return super().init_poolmanager(*args, **kwargs)

# Рабочие зеркала
MIRRORS = [
    "https://hdrezka.ag/", "https://rezka.ag/", "https://hdrezka.me/", 
    "https://hdrezka.sh/", "https://hdrezka.website/", "https://hdrezka.lv/"
]

def create_scraper():
    """Создает сессию с поддержкой прокси и имитацией браузера"""
    s = requests.Session()
    adapter = SSLAdapter()
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.verify = False
    
    s.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
    })
    
    proxy = os.environ.get("PROXY_URL")
    if proxy:
        logger.info(f"Using proxy: {proxy}")
        s.proxies = {"http": proxy, "https": proxy}
    return s

app = FastAPI()

@app.get("/api/search")
async def search(query: str = Query(...)):
    try:
        s = create_scraper()
        rezka_session = HdRezkaSession(MIRRORS[0])
        rezka_session.session = s
        results = rezka_session.search(query)
        return results.all if hasattr(results, 'all') else results
    except Exception as e:
        logger.error(f"Search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/info")
async def get_info(url: str = Query(...)):
    try:
        s = create_scraper()
        rezka_session = HdRezkaSession(MIRRORS[0])
        rezka_session.session = s
        rezka = rezka_session.get(url)
        info = {
            "title": rezka.name,
            "poster": rezka.thumbnail,
            "type": str(rezka.type),
            "translators": rezka.translators_names,
            "description": rezka.description,
            "rating": rezka.rating.value if hasattr(rezka, 'rating') else "-"
        }
        if "series" in str(rezka.type).lower():
            info["seriesInfo"] = rezka.seriesInfo
        return info
    except Exception as e:
        logger.error(f"Info error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/stream")
async def get_stream(url: str = Query(...), translator_id: str = None, season: str = None, episode: str = None):
    try:
        if not url.startswith('http'):
            url = MIRRORS[0].rstrip('/') + ("" if url.startswith('/') else "/") + url
            
        s = create_scraper()
        rezka_session = HdRezkaSession(MIRRORS[0])
        rezka_session.session = s
        rezka = rezka_session.get(url)
        
        t_id = None if translator_id in [None, "", "null", "undefined"] else translator_id
        
        if "series" in str(rezka.type).lower():
            stream = rezka.getStream(season or "1", episode or "1", translation=t_id)
        else:
            stream = rezka.getStream(translation=t_id)
            
        return {
            "videos": stream.videos if stream else {},
            "subtitles": stream.subtitles.subtitles if stream and hasattr(stream, 'subtitles') and stream.subtitles else None
        }
    except Exception as e:
        logger.error(f"Stream error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/new")
async def get_new(category: str = "last", page: int = 1):
    try:
        s = create_scraper()
        base = MIRRORS[0].rstrip('/')
        url = f"{base}/page/{page}/" if category == "last" else f"{base}/{category}/page/{page}/"
        
        response = s.get(url, timeout=45)
        if response.status_code != 200: return []

        soup = BeautifulSoup(response.text, 'html.parser')
        items = soup.find_all('div', class_='b-content__inline_item')
        results = []
        for item in items:
            try:
                link_el = item.find('div', class_='b-content__inline_item-link').find('a')
                img_el = item.find('img')
                results.append({
                    "title": link_el.text.strip(),
                    "url": link_el['href'],
                    "image": img_el.get('src') if img_el else None,
                    "rating": item.find('span', class_='rating').text.strip() if item.find('span', class_='rating') else "-",
                    "category": item.find('i', class_='entity').text.strip() if item.find('i', class_='entity') else "Видео"
                })
            except: continue
        return results
    except Exception as e:
        logger.error(f"New list error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Статические файлы
if not os.path.exists("static"): os.makedirs("static")
@app.get("/")
async def read_index(): return FileResponse('static/index.html')
app.mount("/static", StaticFiles(directory="static"), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
    
