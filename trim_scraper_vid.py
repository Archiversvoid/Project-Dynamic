import yt_dlp

DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

def scrape(url):
    res = {
        "ok": False, 
        "error": "Unknown error", 
        "title": "", 
        "duration": 0, 
        "uploader": "", 
        "thumbnail": "", 
        "video_formats": [], 
        "stream_url": "", 
        "stream_headers": {}
    }
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'noplaylist': True,
        'user_agent': DEFAULT_UA,
        'extractor_args': {'youtube': ['lang=en', 'player_client=web']},
        'http_headers': {'Accept-Language': 'en-US,en;q=0.9'}
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            inf = ydl.extract_info(url, download=False)
            
            res["ok"] = True
            res["title"] = inf.get("title", "Unknown Title")
            res["duration"] = inf.get("duration", 0)
            res["uploader"] = inf.get("uploader", "")
            res["thumbnail"] = inf.get("thumbnail", "")
            
            formats = inf.get('formats') or []
            valid_streams = [f for f in formats if f.get('url') and f.get('url').startswith('http')]
            
            mapped_vids = []
            for f in formats:
                vcodec = f.get("vcodec")
                acodec = f.get("acodec")
                fid = f.get("format_id")
                
                if "storyboard" in f.get("format_note", "").lower() or not f.get("url"):
                    continue

                if vcodec != "none" and vcodec is not None:
                    h = f.get("height") or 0
                    if h < 144: continue
                    
                    has_audio = acodec != "none" and acodec is not None
                    final_fid = str(fid) if has_audio else f"{fid}+ba/b"
                    
                    lbl = f"{h}p" if h > 0 else (f.get("format_note") or f.get("resolution") or str(fid))
                    if f.get("fps") and f.get("fps") > 30 and h > 0:
                        lbl += f"{int(f.get('fps'))}"
                    if not has_audio:
                        lbl += " (Requires Muxing)"
                    
                    fmt = {
                        "format_id": final_fid,
                        "url": f.get("url"),
                        "label": str(lbl),
                        "vcodec": vcodec,
                        "acodec": acodec if has_audio else "none",
                        "height": h,
                        "ext": f.get("ext", ""),
                        "tbr": f.get("tbr") or f.get("vbr") or 0,
                        "filesize": f.get("filesize"),
                        "filesize_approx": f.get("filesize_approx"),
                        "thumbnail": inf.get("thumbnail", "")
                    }
                    mapped_vids.append(fmt)

            mapped_vids.sort(key=lambda x: (x.get("height") or 0, x["ext"] == "mp4"), reverse=True)
            seen_h = set()
            for v in mapped_vids:
                h_val = v.get("height") or 0
                if h_val not in seen_h:
                    seen_h.add(h_val)
                    res["video_formats"].append(v)

            muxed = [f for f in valid_streams if f.get('vcodec') != 'none' and f.get('acodec') != 'none' 
                     and 'avc1' in (f.get('vcodec') or '').lower()]
            
            capped_muxed = [s for s in muxed if 0 < (s.get('height') or 0) <= 480]
            
            if capped_muxed:
                best_preview = sorted(capped_muxed, key=lambda x: x.get('height') or 0, reverse=True)[0]
                res["stream_url"] = best_preview.get('url', '')
                res["stream_headers"] = best_preview.get('http_headers', {})
            elif muxed:
                best_preview = sorted(muxed, key=lambda x: x.get('height') or 9999)[0]
                res["stream_url"] = best_preview.get('url', '')
                res["stream_headers"] = best_preview.get('http_headers', {})
            
    except Exception as e:
        res = {"ok": False, "error": f"Video scraping failed: {str(e)}"}
        
    return res