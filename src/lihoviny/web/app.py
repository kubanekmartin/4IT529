"""Web: FastAPI se šablonami vykreslenými na serveru, jen čte z Postgresu.

Nová stránka = modul s `router` v tomto balíčku, šablona dědící z `base.html`
a řádek v `PAGES` (odkaz v navigaci se přidá sám).
"""

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse

from lihoviny.web import (
    akcni_ceny,
    architektura,
    behy,
    brief,
    komunikace,
    log,
    prehled,
    regulace,
    sortiment,
)
from lihoviny.web.common import templates

# (router, cesta v navigaci, popisek v navigaci)
PAGES = [
    (prehled.router, "/prehled", "Přehled"),
    (brief.router, "/brief", "Brief"),
    (sortiment.router, "/sortiment", "Sortiment"),
    (akcni_ceny.router, "/akcni-ceny", "Akční ceny"),
    (komunikace.router, "/komunikace", "Komunikace"),
    (regulace.router, "/regulace", "Regulace"),
    (log.router, "/log", "Log komunikace"),
    (behy.router, "/behy", "Běhy"),
    (architektura.router, "/architektura", "Architektura"),
]


def create_app(database_url: str) -> FastAPI:
    app = FastAPI(title="Lihoviny MAS", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.database_url = database_url
    templates.env.globals["nav"] = [(path, label) for _, path, label in PAGES]

    @app.middleware("http")
    async def no_indexing(request: Request, call_next):
        # Studijní prototyp s daty třetích stran: vyhledávače ho nemají indexovat.
        response = await call_next(request)
        response.headers["X-Robots-Tag"] = "noindex"
        return response

    @app.get("/")
    def home():
        return RedirectResponse("/prehled")

    for router, _, _ in PAGES:
        app.include_router(router)
    return app
