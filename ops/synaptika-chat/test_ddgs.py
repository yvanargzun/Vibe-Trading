#!/usr/bin/env python3
from ddgs import DDGS

print("=== text search ===")
try:
    r = list(DDGS().text("noticias internacionales hoy", max_results=5))
    for i, x in enumerate(r):
        print(i, x.get("title"), "|", x.get("href") or x.get("link"))
except Exception as e:
    print("text_err", e)

print("=== news search ===")
try:
    r = list(DDGS().news("international news", max_results=5))
    for i, x in enumerate(r):
        print(i, x.get("title"), "|", x.get("url") or x.get("href"), "|", x.get("source"))
except Exception as e:
    print("news_err", e)

print("=== text en ===")
try:
    r = list(DDGS().text("top world news headlines today", max_results=5))
    for i, x in enumerate(r):
        print(i, x.get("title"), "|", x.get("href") or x.get("link"))
except Exception as e:
    print("text_en_err", e)
