"""Web: FastAPI se šablonami vykreslenými na serveru, jen čte z Postgresu.

Nová stránka = modul s `router` v tomto balíčku, šablona dědící z `base.html`
a řádek v `PAGES` (odkaz v navigaci se přidá sám).
"""

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse

from lihoviny.web import akcni_ceny, log
from lihoviny.web.common import templates

# (router, cesta v navigaci, popisek v navigaci)
PAGES = [
    (akcni_ceny.router, "/akcni-ceny", "Akční ceny"),
    (log.router, "/log", "Log komunikace"),
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
        return RedirectResponse("/log")

    for router, _, _ in PAGES:
        app.include_router(router)
    return app
