import re
import aiohttp
import warnings
import logging
from io import BytesIO
from PIL import Image
from info import DREAMXBOTZ_IMAGE_FETCH, TMDB_API_KEY
from imdb import Cinemagoer


logger = logging.getLogger(__name__)
ia = Cinemagoer()
LONG_IMDB_DESCRIPTION = False

Image.MAX_IMAGE_PIXELS = None
warnings.simplefilter("ignore", Image.DecompressionBombWarning)

_session: aiohttp.ClientSession | None = None


async def get_session():
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session


async def fetch_image(url, size=(860, 1200)):
    if not DREAMXBOTZ_IMAGE_FETCH:
        logger.info("Image fetching is disabled.")
        return url

    try:
        session = await get_session()

        async with session.get(url) as response:
            if response.status != 200:
                logger.error(f"Failed to fetch image: {response.status} for {url}")
                return None

            data = await response.read()
            img = Image.open(BytesIO(data))
            img = img.resize(size, Image.LANCZOS)

            out = BytesIO()
            img.save(out, format="JPEG")
            out.seek(0)
            return out

    except aiohttp.ClientError as e:
        logger.error(f"HTTP request error in fetch_image: {e}")
    except IOError as e:
        logger.error(f"I/O error in fetch_image: {e}")
    except Exception as e:
        logger.error(f"Unexpected error in fetch_image: {e}")

    return None


async def close_session():
    global _session
    if _session and not _session.closed:
        await _session.close()

def list_to_str(lst):
    if lst:
        return ", ".join(map(str, lst))
    return ""

async def get_movie_details(query, id=False, file=None):
    try:
        if not id:
            query = query.strip().lower()
            title = query
            year = re.findall(r'[1-2]\d{3}$', query, re.IGNORECASE)
            if year:
                year = list_to_str(year[:1])
                title = query.replace(year, "").strip()
            elif file is not None:
                year = re.findall(r'[1-2]\d{3}', file, re.IGNORECASE)
                if year:
                    year = list_to_str(year[:1])
            else:
                year = None
            movieid = ia.search_movie(title.lower(), results=10)
            if not movieid:
                # retry without year
                movieid = ia.search_movie(title.lower().replace(year or "", "").strip(), results=10) if year else []
            if not movieid:
                return None
            if year:
                filtered = list(filter(lambda k: str(k.get('year')) == str(year), movieid))
                if not filtered:
                    filtered = movieid
            else:
                filtered = movieid
            
            filtered_kind = list(filter(lambda k: k.get('kind') in ['movie', 'tv series'], filtered))
            if not filtered_kind:
                logger.info("No matches found for kind 'movie' or 'tv series', falling back to filtered list.")
                movieid = filtered
            else:
                movieid = filtered_kind
            
            movieid = movieid[0].movieID
        else:
            movieid = query
        movie = ia.get_movie(movieid)
        ia.update(movie, info=['main', 'vote details'])
        
        if movie.get("original air date"):
            date = movie["original air date"]
        elif movie.get("year"):
            date = movie.get("year")
        else:
            date = "N/A"
            
        plot = movie.get('plot')
        if plot and len(plot) > 0:
            plot = plot[0]
        else:
            plot = movie.get('plot outline')
        if plot and len(plot) > 800:
            plot = plot[:800] + "..."
            
        poster_url = movie.get('full-size cover url')
        if poster_url:
            poster_url = poster_url + "._V1_SX1440.jpg" if poster_url.endswith("@.jpg") else poster_url

        # Return raw rating (float or None) — never "N/A" string
        # so channel.py can cleanly detect missing rating and fall to TMDB
        raw_rating = movie.get("rating")
        rating = float(raw_rating) if raw_rating is not None else None

        return {
            'title': movie.get('title'),
            'votes': movie.get('votes'),
            "aka": list_to_str(movie.get("akas")),
            "seasons": movie.get("number of seasons"),
            "box_office": movie.get('box office'),
            'localized_title': movie.get('localized title'),
            'kind': movie.get("kind"),
            "imdb_id": f"tt{movie.get('imdbID')}",
            "cast": list_to_str(movie.get("cast")),
            "runtime": list_to_str(movie.get("runtimes")),
            "countries": list_to_str(movie.get("countries")),
            "certificates": list_to_str(movie.get("certificates")),
            "languages": list_to_str(movie.get("languages")),
            "director": list_to_str(movie.get("director")),
            "writer": list_to_str(movie.get("writer")),
            "producer": list_to_str(movie.get("producer")),
            "composer": list_to_str(movie.get("composer")),
            "cinematographer": list_to_str(movie.get("cinematographer")),
            "music_team": list_to_str(movie.get("music department")),
            "distributors": list_to_str(movie.get("distributors")),
            'release_date': date,
            'year': movie.get('year'),
            'genres': list_to_str(movie.get("genres")),
            'poster_url': poster_url,
            'plot': plot,
            'rating': rating,
            'url': f'https://www.imdb.com/title/tt{movieid}'
        }
    except Exception as e:
        logger.exception(f"An error occurred in get_movie_details: {e}")
        return None

async def _search_youtube_trailer(session: aiohttp.ClientSession, title: str, year: int = None) -> str:
    """Search YouTube for a trailer using the InvidiousAPI (no API key needed)."""
    try:
        query = f"{title} {year} official trailer" if year else f"{title} official trailer"
        # Use Invidious public API — no key required
        invidious_instances = [
            "https://invidious.io.lol",
            "https://inv.nadeko.net",
            "https://invidious.nerdvpn.de",
        ]
        for base in invidious_instances:
            try:
                url = f"{base}/api/v1/search"
                params = {"q": query, "type": "video", "fields": "videoId,title", "page": 1}
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        results = await resp.json()
                        if results and isinstance(results, list):
                            video_id = results[0].get("videoId")
                            if video_id:
                                return f"https://www.youtube.com/watch?v={video_id}"
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"YouTube trailer search failed for '{title}': {e}")
    return None


async def get_movie_detailsx(query, id=False, file=None):
    q = str(query).strip()
    try:
        async with aiohttp.ClientSession() as session:
            search_url = "https://api.themoviedb.org/3/search/multi"
            # Try with full query first
            params = {"api_key": TMDB_API_KEY, "query": q, "language": "en-US"}
            async with session.get(search_url, params=params) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                results = data.get("results", [])

            # If no results, try without year
            if not results:
                q_no_year = re.sub(r'\b(19|20)\d{2}\b', '', q).strip()
                if q_no_year != q:
                    params = {"api_key": TMDB_API_KEY, "query": q_no_year, "language": "en-US"}
                    async with session.get(search_url, params=params) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            results = data.get("results", [])
                            logger.info(f"TMDB retry without year '{q_no_year}': {len(results)} results")

            if not results:
                logger.warning(f"No TMDB results for: {q}")
                return None

            # Skip 'person' results
            result = next((r for r in results if r.get("media_type") in ("movie", "tv")), None)
            if not result:
                logger.warning(f"No movie/tv result in TMDB for: {q}")
                return None

            tmdb_id = result.get("id")
            media_type = result.get("media_type") or "movie"

            # append_to_response=videos,external_ids fetches trailer + imdb_id in same request
            detail_url = f"https://api.themoviedb.org/3/{media_type}/{tmdb_id}"
            params = {"api_key": TMDB_API_KEY, "language": "en-US", "append_to_response": "videos,external_ids"}
            async with session.get(detail_url, params=params) as resp:
                if resp.status != 200:
                    return None
                detail = await resp.json()

            poster = detail.get("poster_path")
            backdrop = detail.get("backdrop_path")
            genres = [g["name"] for g in detail.get("genres", [])]
            rating = round(detail.get("vote_average", 0), 1)
            year = (detail.get("release_date") or detail.get("first_air_date") or "")[:4]
            title = detail.get("title") or detail.get("name", q)
            plot = detail.get("overview", "")
            tmdb_url = f"https://www.themoviedb.org/{media_type}/{tmdb_id}"

            # IMDB ID from external_ids → build IMDB link
            imdb_id = detail.get("external_ids", {}).get("imdb_id", "")
            imdb_url = f"https://www.imdb.com/title/{imdb_id}" if imdb_id else ""

            # YouTube trailer: official first, then any trailer, then any video
            trailer_url = None
            videos = detail.get("videos", {}).get("results", [])
            for v in videos:
                if v.get("site") == "YouTube" and v.get("type") == "Trailer" and v.get("official"):
                    trailer_url = f"https://www.youtube.com/watch?v={v['key']}"
                    break
            if not trailer_url:
                for v in videos:
                    if v.get("site") == "YouTube" and v.get("type") == "Trailer":
                        trailer_url = f"https://www.youtube.com/watch?v={v['key']}"
                        break
            if not trailer_url:
                for v in videos:
                    if v.get("site") == "YouTube":
                        trailer_url = f"https://www.youtube.com/watch?v={v['key']}"
                        break

            # Fallback: YouTube search if TMDB has no trailer
            if not trailer_url:
                trailer_url = await _search_youtube_trailer(session, title, int(year) if year else None)
                if trailer_url:
                    logger.info(f"YouTube search fallback trailer for '{title}': {trailer_url}")

            return {
                "title": title,
                "year": int(year) if year else None,
                "rating": rating,
                "genres": genres,
                "plot": plot,
                "poster_url": f"https://image.tmdb.org/t/p/w1280{poster}" if poster else None,
                "backdrop_url": f"https://image.tmdb.org/t/p/w1280{backdrop}" if backdrop else None,
                "tmdb_url": tmdb_url,
                "tmdb_id": tmdb_id,
                "imdb_id": imdb_id,
                "imdb_url": imdb_url,
                "trailer_url": trailer_url,
            }
    except Exception as e:
        logger.error(f"Direct TMDB error: {e}")
        return None
