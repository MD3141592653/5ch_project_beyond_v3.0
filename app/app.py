"""Local 5ch PostgreSQL archive explorer. Run with: python app.py"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from html import escape
from pathlib import Path
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

DB_HOST = os.environ.get("DB_HOST", "archive-db")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_NAME = os.environ.get("DB_NAME", "archive")
DB_USER = os.environ.get("DB_USER", "muraoka")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "postgre1234")
PORT = int(os.environ.get("PORT", "5000"))

TOPICS = ["コロナ", "円安", "実質賃金"]

CONNINFO = f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} user={DB_USER} password={DB_PASSWORD}"
pool = ConnectionPool(conninfo=CONNINFO, min_size=2, max_size=10, open=False)


def get_conn():
    return pool.connection()


def json_response(start_response, data, status="200 OK"):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    start_response(
        status,
        [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
        ],
    )
    return [body]


def query_param(environ, key, default=""):
    return parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True).get(key, [default])[0]


def search_posts(cur, words, limit=80):
    if not words:
        return []
    clauses = " OR ".join("(p.content ILIKE %s OR t.title ILIKE %s)" for _ in words)
    params = [val for w in words for val in (f"%{w}%", f"%{w}%")]
    cur.execute(
        f"""
        SELECT p.id, p.board, p.thread_id, p.post_id, p.name,
               to_char(p.post_datetime, 'YYYY-MM-DD"T"HH24:MI:SS') AS date_iso,
               p.uid, p.content, p.post_type, t.title
        FROM posts p
        LEFT JOIN threads t ON t.thread_id = p.thread_id
        WHERE {clauses}
        ORDER BY p.post_datetime DESC NULLS LAST
        LIMIT %s;
        """,
        (*params, limit),
    )
    return cur.fetchall()


def search_threads(cur, words, limit=30):
    if not words:
        return []
    clauses = " OR ".join("(p2.content ILIKE %s OR t2.title ILIKE %s)" for _ in words)
    params = [val for w in words for val in (f"%{w}%", f"%{w}%")]
    cur.execute(
        f"""
        SELECT t.thread_id, t.board, t.title, COUNT(p.id) AS post_count,
               to_char(MIN(p.post_datetime), 'YYYY-MM-DD"T"HH24:MI:SS') AS first_date,
               to_char(MAX(p.post_datetime), 'YYYY-MM-DD"T"HH24:MI:SS') AS last_date
        FROM threads t
        JOIN posts p ON p.thread_id = t.thread_id
        WHERE t.thread_id IN (
            SELECT DISTINCT p2.thread_id
            FROM posts p2
            LEFT JOIN threads t2 ON t2.thread_id = p2.thread_id
            WHERE {clauses}
            LIMIT 1000
        )
        GROUP BY t.thread_id, t.board, t.title
        ORDER BY post_count DESC
        LIMIT %s;
        """,
        (*params, limit),
    )
    return cur.fetchall()


def app(environ, start_response):
    path = environ.get("PATH_INFO", "/")
    if path == "/":
        return html_page(start_response)

    try:
        with get_conn() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                if path == "/api/summary":
                    cur.execute(
                        """
                        SELECT
                            (SELECT COUNT(*) FROM threads) AS threads,
                            (SELECT COUNT(*) FROM posts) AS posts,
                            (SELECT COUNT(DISTINCT uid) FROM posts WHERE uid IS NOT NULL AND uid != '') AS users,
                            (SELECT COUNT(*) FROM posts WHERE post_type = 'system') AS system_posts,
                            (SELECT COUNT(*) FROM source_files WHERE status = 'done') AS files;
                        """
                    )
                    summary = dict(cur.fetchone() or {})
                    summary.setdefault("files", 0)
                    summary["refreshed"] = False
                    return json_response(start_response, summary)

                if path == "/api/search":
                    q = query_param(environ, "q")[:200]
                    words = [w.strip() for w in q.split() if w.strip()]
                    if not words:
                        return json_response(start_response, {"query": q, "total": 0, "posts": [], "threads": []})

                    posts = search_posts(cur, words, limit=80)
                    threads = search_threads(cur, words, limit=30)

                    clauses = " OR ".join("(p.content ILIKE %s OR t.title ILIKE %s)" for _ in words)
                    params = [val for w in words for val in (f"%{w}%", f"%{w}%")]
                    cur.execute(
                        f"""
                        SELECT COUNT(*) AS total
                        FROM posts p
                        LEFT JOIN threads t ON t.thread_id = p.thread_id
                        WHERE {clauses};
                        """,
                        params,
                    )
                    total = cur.fetchone()["total"]

                    return json_response(
                        start_response,
                        {"query": q, "total": total, "posts": posts, "threads": threads},
                    )

                if path == "/api/threads":
                    q = query_param(environ, "q")[:150]
                    term = f"%{q}%"
                    cur.execute(
                        """
                        SELECT t.thread_id, t.board, t.title, t.url,
                               COUNT(p.id) AS post_count,
                               to_char(MIN(p.post_datetime), 'YYYY-MM-DD"T"HH24:MI:SS') AS first_date,
                               to_char(MAX(p.post_datetime), 'YYYY-MM-DD"T"HH24:MI:SS') AS last_date
                        FROM threads t
                        LEFT JOIN posts p ON p.thread_id = t.thread_id
                        WHERE (%s = '' OR t.title ILIKE %s OR t.thread_id ILIKE %s)
                        GROUP BY t.thread_id, t.board, t.title, t.url
                        ORDER BY MAX(p.post_datetime) DESC NULLS LAST
                        LIMIT 300;
                        """,
                        (q, term, term),
                    )
                    rows = cur.fetchall()
                    return json_response(start_response, rows)

                if path.startswith("/api/thread/"):
                    tid = path.rsplit("/", 1)[-1]
                    cur.execute(
                        """
                        SELECT thread_id, board, title, url, raw_sha256,
                               to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS') AS created_at
                        FROM threads WHERE thread_id = %s;
                        """,
                        (tid,),
                    )
                    thread = cur.fetchone()

                    cur.execute(
                        """
                        SELECT id, board, thread_id, post_id, name,
                               to_char(post_datetime, 'YYYY-MM-DD"T"HH24:MI:SS') AS date_iso,
                               uid, content, post_type, content_length
                        FROM posts
                        WHERE thread_id = %s
                        ORDER BY post_id ASC
                        LIMIT 1200;
                        """,
                        (tid,),
                    )
                    posts = cur.fetchall()
                    return json_response(start_response, {"thread": thread, "posts": posts})

                if path == "/api/timeline":
                    cur.execute(
                        """
                        SELECT to_char(post_datetime, 'YYYY-MM') AS month,
                               COUNT(*) AS posts,
                               COUNT(DISTINCT thread_id) AS threads
                        FROM posts
                        WHERE post_datetime IS NOT NULL
                        GROUP BY month
                        ORDER BY month;
                        """
                    )
                    rows = cur.fetchall()

                    topics = []
                    for topic in TOPICS:
                        topic_param = f"%{topic}%"
                        cur.execute(
                            """
                            SELECT to_char(p.post_datetime, 'YYYY-MM') AS month,
                                   COUNT(*) AS cnt
                            FROM posts p
                            LEFT JOIN threads t ON t.thread_id = p.thread_id
                            WHERE p.post_datetime IS NOT NULL AND (p.content ILIKE %s OR t.title ILIKE %s)
                            GROUP BY month;
                            """,
                            (topic_param, topic_param),
                        )
                        month_map = {r["month"]: r["cnt"] for r in cur.fetchall()}
                        counts = [month_map.get(r["month"], 0) for r in rows]
                        topics.append({"topic": topic, "counts": counts})

                    return json_response(start_response, {"months": rows, "topics": topics})

                if path == "/api/keywords":
                    values = []
                    for topic in TOPICS:
                        cur.execute(
                            """
                            SELECT COUNT(*) AS posts, COUNT(DISTINCT thread_id) AS threads
                            FROM posts
                            WHERE content ILIKE %s;
                            """,
                            (f"%{topic}%",),
                        )
                        res = cur.fetchone() or {"posts": 0, "threads": 0}
                        values.append({"keyword": topic, "posts": res["posts"], "threads": res["threads"]})
                    return json_response(start_response, values)

                if path == "/api/network":
                    limit = min(100, max(10, int(query_param(environ, "limit", "40") or 40)))
                    cur.execute(
                        """
                        SELECT uid, COUNT(*) AS post_count, COUNT(DISTINCT thread_id) AS thread_count,
                               to_char(MIN(post_datetime), 'YYYY-MM-DD"T"HH24:MI:SS') AS first_date,
                               to_char(MAX(post_datetime), 'YYYY-MM-DD"T"HH24:MI:SS') AS last_date
                        FROM posts
                        WHERE uid IS NOT NULL AND uid != '' AND post_type = 'normal'
                        GROUP BY uid
                        ORDER BY thread_count DESC, post_count DESC
                        LIMIT %s;
                        """,
                        (limit,),
                    )
                    users = cur.fetchall()
                    ids = [u["uid"] for u in users]
                    edges = []
                    for uid in ids:
                        if len(edges) >= 300:
                            break
                        cur.execute(
                            """
                            SELECT p2.uid, COUNT(DISTINCT p1.thread_id) AS weight
                            FROM posts p1
                            JOIN posts p2 ON p1.thread_id = p2.thread_id AND p1.uid < p2.uid
                            WHERE p1.uid = %s AND p2.uid = ANY(%s)
                            GROUP BY p2.uid
                            ORDER BY weight DESC
                            LIMIT 10;
                            """,
                            (uid, ids),
                        )
                        shared = cur.fetchall()
                        for edge in shared:
                            if edge["weight"]:
                                edges.append({"source": uid, "target": edge["uid"], "weight": edge["weight"]})

                    return json_response(start_response, {"users": users, "edges": edges})

        return json_response(start_response, {"error": "Not found"}, "404 Not Found")
    except Exception as exc:
        return json_response(start_response, {"error": str(exc)}, "500 Internal Server Error")


PAGE = r'''<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>5ch Historical Archive Explorer</title>
<style>
:root{color-scheme:dark;--bg:#090b0a;--panel:#141816;--line:#272d29;--muted:#89938c;--text:#e7ebe8;--green:#18d47b;--green2:#092b1c;--gold:#d7b96d}*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 85% -15%,#12251b 0,transparent 36%),var(--bg);font:15px/1.65 system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--text)}.shell{width:min(1240px,100% - 44px);margin:0 auto;padding:28px 0 60px}.top{display:flex;align-items:center;justify-content:space-between;margin-bottom:33px}.brand{display:flex;align-items:center;gap:12px}.mark{width:36px;height:36px;border:1px solid #315443;border-radius:11px;display:grid;place-items:center;color:var(--green);font-weight:800}.brand h1{font-size:19px;margin:0;letter-spacing:-.3px}.brand small{display:block;color:var(--muted);font-size:11px;letter-spacing:.08em}.badge{border:1px solid #16452f;border-radius:99px;color:var(--green);background:#0c2419;padding:5px 12px;font-weight:600;font-size:12px}.hero{padding:30px 28px 27px;border:1px solid var(--line);border-radius:18px;background:linear-gradient(135deg,#111713,#0d100e 70%);box-shadow:0 18px 60px #0005}.eyebrow{color:var(--green);font-size:12px;letter-spacing:.12em;text-transform:uppercase}.hero h2{font-size:25px;margin:5px 0 17px}.search{display:flex;gap:10px}.search input,.filter{flex:1;min-width:0;background:#080a09;border:1px solid #343c36;color:var(--text);border-radius:11px;padding:13px 15px;font:inherit;outline:none}.search input:focus,.filter:focus{border-color:#27a867;box-shadow:0 0 0 3px #18d47b1a}.button{border:0;border-radius:10px;padding:0 22px;background:var(--green);color:#062011;font-weight:750;font:inherit;cursor:pointer}.button:hover{filter:brightness(1.08)}.hint{font-size:12px;color:var(--muted);margin:9px 2px 0}.chips{display:flex;gap:8px;flex-wrap:wrap;margin-top:15px}.chip{border:1px solid #29332d;background:#101511;color:#bfc9c2;border-radius:20px;padding:4px 10px;font-size:12px;cursor:pointer}.chip:hover{border-color:#177748;color:var(--green)}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:16px 0 26px}.stat{border:1px solid var(--line);background:#111412;border-radius:13px;padding:15px 17px}.stat .label{color:var(--muted);font-size:12px}.stat strong{display:block;font-size:23px;margin:2px 0 0;letter-spacing:-.4px}.stat small{color:#657169;font-size:11px}.layout{display:grid;grid-template-columns:220px minmax(0,1fr);gap:22px}.sidebar{position:sticky;top:20px;align-self:start}.side-title{font-size:11px;color:#68736b;letter-spacing:.12em;margin:4px 10px 10px}.nav{display:grid;gap:4px}.nav button{text-align:left;border:1px solid transparent;border-radius:9px;background:transparent;color:#aab3ac;padding:10px 11px;font:inherit;cursor:pointer}.nav button:hover{background:#131815}.nav button.active{background:#102319;border-color:#193d2a;color:#47e28f}.nav i{font-style:normal;display:inline-block;width:25px;color:#8a9a8e}.side-note{margin:24px 9px;color:#68736b;font-size:11px}.main{min-width:0}.section-head{display:flex;align-items:end;justify-content:space-between;margin:1px 2px 13px}.section-head h2{font-size:19px;margin:0}.section-head p{color:var(--muted);font-size:12px;margin:2px 0 0}.filter{max-width:270px;padding:8px 11px;font-size:13px}.results{display:grid;gap:10px}.card{border:1px solid var(--line);border-radius:13px;background:var(--panel);padding:16px 18px;min-width:0}.card h3{font-size:15px;line-height:1.45;margin:0 0 9px}.card h3 button{padding:0;border:0;background:none;color:var(--text);font:inherit;text-align:left;cursor:pointer}.card h3 button:hover{color:var(--green)}.meta{display:flex;gap:10px;flex-wrap:wrap;color:var(--muted);font-size:11px}.pill{border:1px solid #28332c;color:#9da99f;border-radius:99px;padding:1px 8px}.pill.green{color:var(--green);border-color:#1d5034;background:#0e2117}.excerpt{margin:10px 0 0;color:#c2cbc5;white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}.postrow{border-top:1px solid #252a26;padding:13px 0}.postrow:first-child{border-top:0}.postrow p{margin:8px 0;color:#c5ccc7;white-space:pre-wrap;overflow-wrap:anywhere}.empty{border:1px dashed #303a33;border-radius:12px;padding:35px;text-align:center;color:var(--muted)}.chart-card{border:1px solid var(--line);border-radius:13px;background:var(--panel);padding:17px;overflow:hidden}.chart-card h3{margin:0 0 14px;font-size:14px}.chart{width:100%;height:280px;display:block}.chart text{fill:#89938c;font-size:10px}.table{width:100%;border-collapse:collapse;font-size:13px}.table th,.table td{text-align:left;border-bottom:1px solid #252b27;padding:10px 8px}.table th{color:#849087;font-size:11px;font-weight:600}.table td strong{color:#e2e8e3}.table tr:hover td{background:#121714}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:13px}.thread-link{color:var(--green);text-decoration:none}.loading{color:var(--muted);padding:32px}.node-info{font-size:12px;color:var(--muted)}.footer{border-top:1px solid var(--line);margin-top:40px;padding-top:14px;color:#667169;font-size:11px;display:flex;justify-content:space-between}
@media(max-width:760px){.shell{width:calc(100% - 28px);padding-top:16px}.hero{padding:22px 16px}.hero h2{font-size:21px}.stats{grid-template-columns:repeat(2,1fr);gap:8px}.layout{grid-template-columns:1fr;gap:13px}.sidebar{position:static}.nav{display:flex;overflow:auto;padding-bottom:4px}.nav button{white-space:nowrap;font-size:12px;padding:8px}.nav i{width:auto;margin-right:4px}.side-title,.side-note{display:none}.grid2{grid-template-columns:1fr}.section-head{align-items:start;gap:12px;flex-direction:column}.filter{max-width:none;width:100%}.search{gap:7px}.button{padding:0 15px}}
</style></head><body><div class="shell"><header class="top"><div class="brand"><div class="mark">5ch</div><div><h1>Historical Archive Explorer</h1><small>LOCAL DATA · POSTGRESQL</small></div></div><span class="badge">● PostgreSQL アーカイブ</span></header>
<section class="hero"><div class="eyebrow">Archive search</div><h2>過去のスレッドと投稿を横断して探索</h2><form class="search" id="searchForm"><input id="query" placeholder="キーワードを入力　例：安倍晋三 円安" autocomplete="off"><button class="button">検索する →</button></form><p class="hint">複数キーワードはスペース区切り · 本文とスレッドタイトルを検索</p><div class="chips" id="chips"></div></section>
<section class="stats"><div class="stat"><span class="label">収集スレッド</span><strong id="sThreads">—</strong><small>THREADS</small></div><div class="stat"><span class="label">収集レス</span><strong id="sPosts">—</strong><small>POSTS</small></div><div class="stat"><span class="label">観測されたID</span><strong id="sUsers">—</strong><small>POSTER IDS</small></div><div class="stat"><span class="label">データファイル</span><strong id="sFiles">—</strong><small>JSONL FILES</small></div></section>
<div class="layout"><aside class="sidebar"><div class="side-title">探索メニュー</div><nav class="nav"><button data-view="search" class="active"><i>⌕</i>スレッド・本文検索</button><button data-view="threads"><i>▤</i>スレッド一覧</button><button data-view="timeline"><i>◷</i>話題の推移</button><button data-view="network"><i>⌘</i>投稿IDネットワーク</button><button data-view="keywords"><i>▥</i>キーワード頻度</button></nav><p class="side-note">投稿IDは観測上の一致を示します。同一人物であることを意味しません。</p></aside><main class="main" id="main"><div class="loading">データを読み込んでいます…</div></main></div><footer class="footer"><span>PostgreSQL · pg_trgm trigram search</span><span>日付やIDが欠損した投稿は分析時に適切に除外されます</span></footer></div>
<script>
const $=s=>document.querySelector(s), main=$('#main'), fmt=n=>Number(n||0).toLocaleString('ja-JP');let view='search', lastQuery='';const topics=['安倍晋三','自民党','統一教会','アベノミクス','消費税','原発','ネトウヨ','コロナ','円安','実質賃金'];
$('#chips').innerHTML=topics.slice(0,8).map(x=>`<button class="chip" data-term="${x}">${x}</button>`).join('');
async function api(path){const r=await fetch(path);if(!r.ok)throw Error('データを読み込めませんでした');return r.json()}
function empty(msg){return `<div class="empty">${msg}</div>`}function date(s){return s?s.replace('T',' ').slice(0,16):'日時不明'}
function head(title,sub,extra=''){return `<div class="section-head"><div><h2>${title}</h2><p>${sub}</p></div>${extra}</div>`}
function setView(v){view=v;document.querySelectorAll('.nav button').forEach(b=>b.classList.toggle('active',b.dataset.view===v));render()}
document.querySelectorAll('.nav button').forEach(b=>b.onclick=()=>setView(b.dataset.view));document.addEventListener('click',e=>{if(e.target.matches('[data-term]')){ $('#query').value=e.target.dataset.term;runSearch(e.target.dataset.term)} if(e.target.matches('[data-thread]'))loadThread(e.target.dataset.thread)});
$('#searchForm').onsubmit=e=>{e.preventDefault();runSearch($('#query').value)};
async function runSearch(q){lastQuery=q;view='search';document.querySelectorAll('.nav button').forEach(b=>b.classList.toggle('active',b.dataset.view==='search'));main.innerHTML='<div class="loading">検索しています…</div>';const d=await api('/api/search?q='+encodeURIComponent(q));const extra=`<span class="pill green">${fmt(d.total)} 件</span>`;const threads=d.threads.map(t=>`<article class="card"><h3><button data-thread="${t.thread_id}">${esc(t.title||'（タイトルなし）')}</button></h3><div class="meta"><span class="pill">${esc(t.board)}</span><span>スレッド ${esc(t.thread_id)}</span><span>${fmt(t.post_count)} 投稿</span><span>${date(t.first_date)} — ${date(t.last_date)}</span></div></article>`).join('');const posts=d.posts.map(p=>`<article class="card"><div class="meta"><span class="pill">${esc(p.board)}</span><button class="thread-link" data-thread="${p.thread_id}">${esc(p.title||p.thread_id)}</button><span>#${esc(p.post_id)}</span><span>${date(p.date_iso)}</span><span>${esc(p.uid||'ID欠損')}</span>${p.post_type==='system'?'<span class="pill green">運営メッセージ</span>':''}</div><p class="excerpt">${esc(p.content)}</p></article>`).join('');main.innerHTML=head('検索結果',q?`「${esc(q)}」を本文・タイトルから検索`:'検索語を入力すると、スレッドをまたいで投稿を検索できます',extra)+`<div class="grid2"><div><h3>該当スレッド <span class="pill">${d.threads.length}</span></h3><div class="results">${threads||empty('該当するスレッドはありません')}</div></div><div><h3>該当投稿 <span class="pill">${fmt(d.total)}</span></h3><div class="results">${posts||empty('検索語を入力してください')}</div></div></div>`}
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function render(){if(view==='search'){await runSearch(lastQuery);return}if(view==='threads'){await threadList();return}if(view==='timeline'){await timeline();return}if(view==='network'){await network();return}if(view==='keywords'){await keywords()}}
async function threadList(){main.innerHTML=head('スレッド一覧','収録スレッドをタイトルやスレッドIDで絞り込み','<input class="filter" id="threadFilter" placeholder="タイトル・IDで検索">')+'<div class="results" id="threadRows"><div class="loading">読み込み中…</div></div>';$('#threadFilter').oninput=async e=>showThreads(await api('/api/threads?q='+encodeURIComponent(e.target.value)));showThreads(await api('/api/threads'))}
function showThreads(rows){const el=$('#threadRows');if(!el)return;el.innerHTML=rows.length?rows.map(t=>`<article class="card"><h3><button data-thread="${t.thread_id}">${esc(t.title||'（タイトルなし）')}</button></h3><div class="meta"><span class="pill">${esc(t.board)}</span><span>${esc(t.thread_id)}</span><span>${fmt(t.post_count)} 投稿</span><span>${date(t.first_date)} — ${date(t.last_date)}</span></div></article>`).join(''):empty('スレッドがありません')}
async function loadThread(id){view='thread';main.innerHTML='<div class="loading">スレッドを読み込んでいます…</div>';const d=await api('/api/thread/'+encodeURIComponent(id));if(!d.thread){main.innerHTML=empty('スレッドが見つかりません');return}const t=d.thread;main.innerHTML=`<button class="chip" id="back">← スレッド一覧へ</button>`+head(esc(t.title),'スレッド詳細 · '+esc(t.board)+' / '+esc(t.thread_id))+`<div class="results"><div class="meta"><span class="pill">${fmt(d.posts.length)} 投稿</span><a class="thread-link" target="_blank" rel="noopener" href="${esc(t.url)}">元スレッドを開く ↗</a></div><article class="card">${d.posts.map(p=>`<div class="postrow"><div class="meta"><span>#${esc(p.post_id)}</span><span>${date(p.date_iso)}</span><span>${esc(p.name)}</span><span>${esc(p.uid||'ID欠損')}</span>${p.post_type==='system'?'<span class="pill green">運営メッセージ</span>':''}</div><p>${esc(p.content)}</p></div>`).join('')}</article></div>`;$('#back').onclick=()=>setView('threads')}
async function timeline(){const d=await api('/api/timeline');main.innerHTML=head('話題の推移','月別の投稿数と、キーワードを含む投稿数')+`<div class="chart-card"><h3>月別投稿数</h3><svg id="timelineChart" class="chart" viewBox="0 0 900 280" preserveAspectRatio="none"></svg></div><div class="chart-card" style="margin-top:13px"><h3>キーワード別の投稿推移</h3><svg id="topicChart" class="chart" viewBox="0 0 900 300" preserveAspectRatio="none"></svg></div>`;lineChart('timelineChart',d.months.map(x=>x.posts),d.months.map(x=>x.month),['#19d47b']);lineChart('topicChart',d.months.map((_,i)=>d.topics.slice(0,6).map(x=>x.counts[i])),d.months.map(x=>x.month),['#19d47b','#66b8ff','#f1bb54','#c78cff','#ff7e88','#7cddc4'],d.topics.slice(0,6).map(x=>x.topic))}
function lineChart(id,series,labels,colors,legend=[]){const svg=$('#'+id);if(!svg)return;let sets=Array.isArray(series[0])?series:[series];const W=900,H=280,L=46,R=15,T=18,B=33,max=Math.max(1,...sets.flat());let out='';for(let g=0;g<4;g++){let y=T+(H-T-B)*g/3;out+=`<line x1="${L}" y1="${y}" x2="${W-R}" y2="${y}" stroke="#26302a"/><text x="${L-8}" y="${y+4}" text-anchor="end">${Math.round(max*(3-g)/3)}</text>`}sets.forEach((s,j)=>{let pts=s.map((v,i)=>`${L+i*(W-L-R)/Math.max(s.length-1,1)},${T+(H-T-B)*(1-v/max)}`).join(' ');out+=`<polyline fill="none" stroke="${colors[j]}" stroke-width="2.2" points="${pts}"/>`});labels.forEach((l,i)=>{if(i%Math.max(1,Math.ceil(labels.length/12))===0)out+=`<text x="${L+i*(W-L-R)/Math.max(labels.length-1,1)}" y="${H-8}" text-anchor="middle">${esc(l)}</text>`});if(legend.length)legend.forEach((v,i)=>out+=`<text x="${L+i*135}" y="12" fill="${colors[i]}" style="fill:${colors[i]}">● ${esc(v)}</text>`);svg.innerHTML=out}
async function keywords(){const rows=await api('/api/keywords');main.innerHTML=head('キーワード頻度・共起の手がかり','キーワードを含む投稿数とスレッド数。行を押すと全文検索します')+`<div class="card"><table class="table"><thead><tr><th>キーワード</th><th>投稿数</th><th>スレッド数</th><th>比率</th></tr></thead><tbody>${rows.map(r=>`<tr data-keyword="${esc(r.keyword)}"><td><strong>${esc(r.keyword)}</strong></td><td>${fmt(r.posts)}</td><td>${fmt(r.threads)}</td><td><div style="height:5px;background:#26312a;border-radius:9px"><div style="height:5px;width:${Math.max(1,100*r.posts/Math.max(1,rows[0].posts))}%;background:#19d47b;border-radius:9px"></div></div></td></tr>`).join('')}</tbody></table></div><p class="node-info">頻度集計は本文の部分一致です。見出しでの出現数は含みません。</p>`;main.querySelectorAll('[data-keyword]').forEach(tr=>tr.onclick=()=>{let q=tr.dataset.keyword;$('#query').value=q;runSearch(q)})}
async function network(){const d=await api('/api/network');main.innerHTML=head('投稿IDネットワーク','同じスレッドに現れた投稿IDのつながり。本人確認や個人特定を示すものではありません')+`<div class="grid2"><div class="chart-card"><h3>複数スレッドに参加したID</h3><svg id="networkChart" class="chart" viewBox="0 0 620 350"></svg><p class="node-info">表示対象 ${d.users.length} IDs · 線は同一スレッドに現れた関係</p></div><div class="card"><h3>投稿IDランキング</h3><table class="table"><thead><tr><th>ID</th><th>投稿</th><th>スレッド</th></tr></thead><tbody>${d.users.slice(0,25).map(u=>`<tr><td>${esc(u.uid)}</td><td>${fmt(u.post_count)}</td><td>${fmt(u.thread_count)}</td></tr>`).join('')}</tbody></table></div></div>`;drawNetwork(d)}
function drawNetwork(d){const svg=$('#networkChart');if(!svg)return;const nodes=d.users.slice(0,35).map((u,i)=>({...u,x:310+230*Math.cos(i*2.4),y:170+125*Math.sin(i*2.4)})), map=new Map(nodes.map(n=>[n.uid,n]));let out='';d.edges.forEach(e=>{const a=map.get(e.source),b=map.get(e.target);if(a&&b)out+=`<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="#315b42" stroke-opacity=".6" stroke-width="${Math.min(5,1+e.weight)}"/>`});nodes.forEach(n=>{let r=Math.min(15,5+Math.sqrt(n.post_count));out+=`<circle cx="${n.x}" cy="${n.y}" r="${r}" fill="#19d47b" fill-opacity=".82"/><text x="${n.x}" y="${n.y+r+12}" text-anchor="middle">${esc(n.uid.slice(0,10))}</text>`});svg.innerHTML=out}
async function init(){const s=await api('/api/summary');$('#sThreads').textContent=fmt(s.threads);$('#sPosts').textContent=fmt(s.posts);$('#sUsers').textContent=fmt(s.users);$('#sFiles').textContent=fmt(s.files);await runSearch('')}
init().catch(e=>main.innerHTML=empty(esc(e.message)));
</script></body></html>'''


def html_page(start_response):
    body = PAGE.encode("utf-8")
    start_response("200 OK", [("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(body)))])
    return [body]


if __name__ == "__main__":
    print(f"Connecting to PostgreSQL at {DB_USER}@{DB_HOST}:{DB_PORT}/{DB_NAME}...")
    pool.open()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM threads;")
            t_cnt = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM posts;")
            p_cnt = cur.fetchone()[0]
            print(f"Database connected · {t_cnt} threads · {p_cnt} posts")
    print(f"Server starting on http://0.0.0.0:{PORT}")
    make_server("0.0.0.0", PORT, app).serve_forever()
