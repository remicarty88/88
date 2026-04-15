import logging
import os
import requests
import cloudscraper
import time
import random
import ssl
import urllib3
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from requests.adapters import HTTPAdapter
from urllib3.poolmanager import PoolManager
from HdRezkaApi import HdRezkaApi, HdRezkaSession
from HdRezkaApi.search import HdRezkaSearch

# Отключаем ворнинги SSL, чтобы не забивать логи
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Глобальный патч для SSL
try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Кастомный адаптер для полного отключения проверок SSL
class SSLAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        kwargs["cert_reqs"] = ssl.CERT_NONE
        kwargs["assert_hostname"] = False
        return super().init_poolmanager(*args, **kwargs)

# Список зеркал
MIRRORS = [
    "https://hdrezka.ag/", 
    "https://rezka.ag/", 
    "https://hdrezka.me/", 
    "https://hdrezka.sh/",
    "https://hdrezka.website/",
    "https://hdrezka.ac/",
    "https://hdrezka.lv/"
]
current_mirror_index = 0

def get_random_proxy():
    """Выбор прокси из файла"""
    try:
        proxy_file = "proxies.txt"
        if os.path.exists(proxy_file):
            with open(proxy_file, 'r') as f:
                proxies = [line.strip() for line in f if line.strip()]
                if proxies:
                    return random.choice(proxies)
    except:
        pass
    return None

def create_new_scraper():
    """Создает сессию с поддержкой прокси и имитацией браузера"""
    s = requests.Session()
    adapter = SSLAdapter()
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    
    s.verify = False
    s.trust_env = False
    
    s.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
        'Connection': 'keep-alive',
    })
    
    proxy_url = os.environ.get("PROXY_URL") or get_random_proxy()
    if proxy_url:
        logger.info(f"Using proxy: {proxy_url}")
        s.proxies = {"http": proxy_url, "https": proxy_url}
    return s

scraper = create_new_scraper()

def get_session(mirror_idx=None):
    """Инициализация сессии HdRezkaApi"""
    global current_mirror_index, scraper
    idx = mirror_idx if mirror_idx is not None else current_mirror_index
    origin = MIRRORS[idx].rstrip('/')
    logger.info(f"Using mirror: {origin}")
    s = HdRezkaSession(origin)
    s.session = create_new_scraper()
    return s

session = get_session()

app = FastAPI()

@app.get("/api/search")
async def search(query: str = Query(...), depth: int = 0):
    """Поиск фильмов с глубокой имитацией и ротацией прокси при ошибках"""
    global session, current_mirror_index, scraper
    
    if depth > 5: # Увеличим глубину попыток
        return []

    logger.info(f"Searching for: '{query}' [Attempt: {depth}] using mirror: {session.origin}")
    
    try:
        # Пробуем выполнить поиск
        results_list = []
        # ... (код поиска остается прежним)
        try:
            search_results = session.search(query)
            if hasattr(search_results, 'all'):
                results_list = search_results.all
            elif isinstance(search_results, list):
                results_list = search_results
        except:
            pass

        # 2. Прямой AJAX поиск
        if not results_list:
            ajax_url = f"{session.origin.rstrip('/')}/engine/ajax/search.php"
            response = scraper.get(ajax_url, params={'q': query}, timeout=15)
            
            if response.status_code == 200 and response.text:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(response.text, 'html.parser')
                items = soup.find_all('li')
                for item in items:
                    link_tag = item.find('a')
                    if link_tag:
                        title = link_tag.find('span', class_='title').text if link_tag.find('span', class_='title') else link_tag.text
                        url = link_tag['href']
                        if not url.startswith('http'):
                            url = session.origin.rstrip('/') + url
                        
                    # Попробуем найти изображение
                        image_url = None
                        
                        # 1. Проверяем тег img
                        img_tag = item.find('img')
                        if img_tag:
                            image_url = img_tag.get('src') or img_tag.get('data-src') or img_tag.get('data-original')
                        
                        # 2. Если не нашли, ищем в стиле background-image
                        if not image_url:
                            elements_with_style = item.find_all(lambda tag: tag.has_attr('style') and 'background-image' in tag['style'])
                            if item.has_attr('style') and 'background-image' in item['style']:
                                elements_with_style.insert(0, item)
                                
                            for el in elements_with_style:
                                import re
                                match = re.search(r'url\((.*?)\)', el['style'])
                                if match:
                                    image_url = match.group(1).strip("'\" ")
                                    break
                        
                        # 3. Если все еще не нашли, попробуем найти по классу
                        if not image_url:
                            import re
                            pic_div = item.find(class_=re.compile("picture|image|thumb|cell-img"))
                            if pic_div and pic_div.find('img'):
                                img = pic_div.find('img')
                                image_url = img.get('src') or img.get('data-src')

                        # Очистка и нормализация URL
                        if image_url:
                            image_url = image_url.strip()
                            # Принудительно очищаем от лишних кавычек и пробелов
                            image_url = image_url.replace('"', '').replace("'", "").strip()
                            
                            # Исправление протоколов и доменов
                            if image_url.startswith('//'):
                                image_url = 'https:' + image_url
                            elif not image_url.startswith('http'):
                                # Если путь относительный, добавляем текущее зеркало
                                origin = session.origin.rstrip('/')
                                if not image_url.startswith('/'):
                                    image_url = '/' + image_url
                                image_url = origin + image_url
                            
                            # Принудительно меняем http на https для CDN rezka
                            if 'hdrezka' in image_url or 'rezka' in image_url:
                                image_url = image_url.replace('http://', 'https://')
                        
                        results.append({
                            "title": title.strip(), "url": url,
                            "rating": item.find('span', class_='rating').text if item.find('span', class_='rating') else "-",
                            "category": item.find('span', class_='info').text if item.find('span', class_='info') else "Found via AJAX",
                            "image": image_url
                        })

                results_list = results

        # 3. Если всё еще пусто, меняем зеркало и скрейпер
        if not results_list:
            current_mirror_index = (current_mirror_index + 1) % len(MIRRORS)
            scraper = create_new_scraper() # Новый отпечаток браузера
            session = get_session()
            return await search(query, depth + 1)

        return results_list
        
    except Exception as e:
        logger.error(f"Search failed: {str(e)}")
        if "Connection" in str(e) or "reset" in str(e):
            current_mirror_index = (current_mirror_index + 1) % len(MIRRORS)
            session = get_session()
            return await search(query, depth + 1)
        return []

# Глобальный кэш для обложек и инфо
INFO_CACHE = {}

@app.get("/api/info")
async def get_info(url: str = Query(...)):
    """Получение информации с кэшированием"""
    global INFO_CACHE
    
    if url in INFO_CACHE:
        # Если данные свежие (меньше 1 часа), отдаем из кэша
        cached_time, data = INFO_CACHE[url]
        if time.time() - cached_time < 3600:
            return data

    try:
        logger.info(f"Getting info for: {url}")
        rezka = session.get(url)
        
        if not rezka.ok:
            # Если в кэше есть старые данные, отдаем их при ошибке
            if url in INFO_CACHE:
                return INFO_CACHE[url][1]
            raise HTTPException(status_code=400, detail=str(rezka.exception))

        info = {
            "title": rezka.name,
            "poster": rezka.thumbnail,
            "poster_hq": rezka.thumbnailHQ if hasattr(rezka, 'thumbnailHQ') else rezka.thumbnail,
            "type": str(rezka.type),
            "translators": rezka.translators_names,
            "description": rezka.description,
            "rating": rezka.rating.value if hasattr(rezka, 'rating') else "-"
        }

        # Принудительно собираем информацию о сериях для всех переводчиков
        if "tv_series" in str(rezka.type).lower() or "series" in str(rezka.type).lower():
            try:
                # Библиотека HdRezkaApi имеет свойство seriesInfo, которое делает запросы
                info["seriesInfo"] = rezka.seriesInfo
            except Exception as e:
                logger.error(f"Error fetching seriesInfo: {e}")
                info["seriesInfo"] = {}
        
        # Сохраняем в кэш
        INFO_CACHE[url] = (time.time(), info)
        return info
    except Exception as e:
        logger.error(f"Info error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/stream")
async def get_stream(url: str = Query(...), translator_id: str = None, season: str = None, episode: str = None):
    """Получение потока через cloudscraper"""
    try:
        logger.info(f"Getting stream for: {url}, translator: {translator_id}, s: {season}, e: {episode}")
        rezka = session.get(url)
        
        if "tv_series" in str(rezka.type):
            if not season or not episode:
                season, episode = "1", "1"
            stream = rezka.getStream(season, episode, translation=translator_id)
        else:
            stream = rezka.getStream(translation=translator_id)
            
        return {
            "videos": stream.videos,
            "subtitles": stream.subtitles.subtitles if hasattr(stream, 'subtitles') and stream.subtitles else None
        }
    except Exception as e:
        logger.error(f"Stream error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/new")
async def get_new(category: str = "last", page: int = 1, depth: int = 0):
    """Получение новинок с ротацией прокси, ограничено для предотвращения 502 ошибки"""
    global session, scraper
    
    # Ограничиваем до 3 попыток, чтобы уложиться в лимит времени Railway
    if depth > 3: 
        return []

    try:
        base_origin = session.origin.rstrip('/')
        if category == "last":
            url = f"{base_origin}/page/{page}/" if page > 1 else f"{base_origin}/"
        else:
            url = f"{base_origin}/{category}/page/{page}/"
            
        logger.info(f"Fetching [Attempt {depth}]: {url}")
        
        # Уменьшаем таймаут до 10 секунд
        response = scraper.get(url, timeout=10, verify=False)
        
        if response.status_code == 200:
            # ... (парсинг)
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(response.text, 'html.parser')
            items = soup.find_all('div', class_='b-content__inline_item')
            results = []
            
            for item in items:
                try:
                    link_container = item.find('div', class_='b-content__inline_item-link')
                    if not link_container: continue
                    
                    link = link_container.find('a')
                    img = item.find('img')
                    
                    # Извлечение рейтинга
                    rating = "-"
                    rating_el = item.find('span', class_='rating')
                    if rating_el:
                        rating = rating_el.text.strip()
                    
                    # Извлечение категории/сущности
                    entity = "Видео"
                    entity_el = item.find('i', class_='entity')
                    if entity_el:
                        entity = entity_el.text.strip()

                    results.append({
                        "title": link.text.strip(),
                        "url": link['href'],
                        "image": img.get('src') if img else None,
                        "rating": rating,
                        "category": entity
                    })
                except Exception as e:
                    logger.error(f"Error parsing item: {e}")
                    continue
            return results
        
        # Если статус 403, пробуем другое зеркало и новый прокси
        if response.status_code == 403:
            logger.warning(f"Mirror {session.origin} returned 403, rotating mirror and proxy...")
            current_mirror_index = (current_mirror_index + 1) % len(MIRRORS)
            scraper = create_new_scraper()
            session = get_session()
            return await get_new(category, page, depth + 1)
        
        # Если другой плохой статус
        logger.warning(f"Bad status {response.status_code}, retrying with new proxy...")
        scraper = create_new_scraper()
        session = get_session()
        return await get_new(category, page, depth + 1)

    except Exception as e:
        logger.error(f"Failed to fetch new content: {str(e)}")
        # При любой ошибке (SSL, Connection, Timeout) меняем прокси и пробуем снова
        scraper = create_new_scraper()
        session = get_session()
        return await get_new(category, page, depth + 1)

# Статические файлы (фронтенд)
if not os.path.exists("static"):
    os.makedirs("static")

@app.get("/")
async def read_index():
    return FileResponse('static/index.html')

app.mount("/static", StaticFiles(directory="static"), name="static")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

