import yt_dlp

DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

def scrape(url):
    res = {
        "ok": False, 
        "error": "Unknown error", 
        "title": "", 
        "duration": 0, 
        "thumbnail": "", 
        "audio_formats": [], 
        "stream_url": "", 
        "stream_headers": {}
    }
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'noplaylist': True,
        'user_agent': DEFAULT_UA,
        'extractor_args': {'youtube': ['player_client=android', 'player_client=web']},
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            inf = ydl.extract_info(url, download=False)
            
            res["ok"] = True
            res["title"] = inf.get("title", "Unknown Title")
            res["duration"] = inf.get("duration", 0)
            res["thumbnail"] = inf.get("thumbnail", "")
            
            formats = inf.get('formats') or []
            valid_streams = [f for f in formats if f.get('url') and f.get('url').startswith('http')]
            
            mapped_auds = []
            for f in formats:
                vcodec = f.get("vcodec")
                acodec = f.get("acodec")
                fid = f.get("format_id")
                
                if acodec != "none" and acodec is not None and (vcodec == "none" or vcodec is None):
                    abr = f.get("abr") or 0
                    lbl = f"{int(abr)}kbps" if abr else (f.get("format_note") or str(fid))
                    fmt = {
                        "format_id": str(fid),
                        "url": f.get("url"),
                        "label": str(lbl),
                        "ext": f.get("ext", ""),
                        "abr": abr,
                        "tbr": f.get("tbr") or abr,
                        "filesize": f.get("filesize"),
                        "filesize_approx": f.get("filesize_approx"),
                        "thumbnail": inf.get("thumbnail", "")
                    }
                    mapped_auds.append(fmt)

            mapped_auds.sort(key=lambda x: (x.get("abr") or 0, x["ext"] == "m4a"), reverse=True)
            seen_abr = set()
            for a in mapped_auds:
                abr = a.get("abr") or 0
                if abr not in seen_abr and abr > 0:
                    seen_abr.add(abr)
                    res["audio_formats"].append(a)

            auds_only = [f for f in valid_streams if f.get('acodec') != 'none' and f.get('vcodec') == 'none']
            if auds_only:
                m4a_streams = [s for s in auds_only if s.get('ext') == 'm4a']
                if m4a_streams:
                    best_aud = sorted(m4a_streams, key=lambda x: x.get('abr') or 0, reverse=True)[0]
                else:
                    best_aud = sorted(auds_only, key=lambda x: x.get('abr') or 0, reverse=True)[0]
                    
                res["stream_url"] = best_aud.get('url', '')
                res["stream_headers"] = best_aud.get('http_headers', {})

    except Exception as e:
        res = {"ok": False, "error": f"Audio scraping failed: {str(e)}"}
        
    return res