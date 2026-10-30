"""Páginas públicas que representan EduLab ante los buscadores."""
from xml.etree.ElementTree import Element, SubElement, tostring

from flask import Response, request

ORIGIN = 'https://edulab.wiki'
PUBLIC_PAGES = {'welcome': '/', 'privacy': '/privacy', 'terms': '/terms'}
TITLE = 'EduLab · Informes, talleres y glosarios con IA'
DESCRIPTION = ('Crea informes, talleres y glosarios con inteligencia artificial. '
               'Portadas para instituciones venezolanas y documentos editables en Word y PDF.')
SCHEMA = {
    '@context': 'https://schema.org',
    '@graph': [
        {'@type': 'WebSite', '@id': ORIGIN + '/#website', 'url': ORIGIN + '/',
         'name': 'EduLab', 'alternateName': ['EduLab Wiki', 'EduLab.wiki'], 'inLanguage': 'es',
         'publisher': {'@id': ORIGIN + '/#organization'}},
        {'@type': 'Organization', '@id': ORIGIN + '/#organization', 'name': 'EduLab',
         'url': ORIGIN + '/', 'logo': ORIGIN + '/static/img/icon.png',
         'email': 'edulab.wiki@gmail.com'},
    ],
}


def init_app(app):
    @app.context_processor
    def seo_metadata():
        path = PUBLIC_PAGES.get(request.endpoint)
        return {'seo_canonical': ORIGIN + path if path is not None else None,
                'seo_auth_page': request.blueprint == 'auth',
                'seo_title': TITLE, 'seo_description': DESCRIPTION, 'seo_schema': SCHEMA}

    @app.after_request
    def indexing_policy(response):
        # Google debe poder rastrear /login para leer noindex y retirarlo del índice.
        if request.endpoint not in (*PUBLIC_PAGES, 'static', 'robots_txt', 'sitemap_xml') or response.status_code >= 400:
            response.headers['X-Robots-Tag'] = 'noindex, follow'
        return response

    @app.get('/robots.txt')
    def robots_txt():
        return Response('User-agent: *\nAllow: /\n\nSitemap: ' + ORIGIN + '/sitemap.xml\n',
                        mimetype='text/plain')

    @app.get('/sitemap.xml')
    def sitemap_xml():
        root = Element('urlset', xmlns='http://www.sitemaps.org/schemas/sitemap/0.9')
        for path in PUBLIC_PAGES.values():
            SubElement(SubElement(root, 'url'), 'loc').text = ORIGIN + path
        return Response(tostring(root, encoding='utf-8', xml_declaration=True), mimetype='application/xml')
