# TypeBlitz SEO & Store Badge Checklist

## ✅ Microsoft Store Badge

- Added Microsoft Store badge loader script in [index.html](index.html).
- Added live `<ms-store-badge>` in [src/components/Download.tsx](src/components/Download.tsx) with:
  - `productid="9nc8mkgl2q2b"`
  - `productname="TypeBlitz"`
  - `window-mode="direct"`
  - `theme="auto"`
  - `size="large"`
  - `language="en"`
  - `animation="on"`
- Added fallback direct Microsoft Store link for resilience.
- Added TypeScript declaration for custom element in [src/vite-env-custom.d.ts](src/vite-env-custom.d.ts).

## ✅ Technical SEO

- Added route-level SEO manager in [src/components/SeoHead.tsx](src/components/SeoHead.tsx).
- Implemented page-level:
  - Meta title and description
  - Canonical URL
  - Open Graph tags
  - Twitter card tags
  - Robots directives
  - JSON-LD structured data (homepage + quickstart)
- Integrated SEO tags on:
  - [src/pages/Index.tsx](src/pages/Index.tsx)
  - [src/pages/Quickstart.tsx](src/pages/Quickstart.tsx)
  - [src/pages/Privacy.tsx](src/pages/Privacy.tsx)
  - [src/pages/Terms.tsx](src/pages/Terms.tsx)
  - [src/pages/Eula.tsx](src/pages/Eula.tsx)
  - [src/pages/NotFound.tsx](src/pages/NotFound.tsx) (`noindex,nofollow`)
- Removed conflicting global SEO injections from:
  - [src/main.tsx](src/main.tsx)
  - [src/App.tsx](src/App.tsx)

## ✅ Multi-XML Sitemap Structure

- Sitemap index:
  - [public/sitemap.xml](public/sitemap.xml)
- Segmented sitemaps:
  - [public/sitemaps/sitemap-pages.xml](public/sitemaps/sitemap-pages.xml)
  - [public/sitemaps/sitemap-legal.xml](public/sitemaps/sitemap-legal.xml)
  - [public/sitemaps/sitemap-media.xml](public/sitemaps/sitemap-media.xml)

## ✅ robots.txt

- Updated [public/robots.txt](public/robots.txt) to include all sitemap URLs.

## ✅ Crawlability & Indexability

- Core public routes included in segmented sitemaps.
- Robots allows crawling for major bots and all agents.
- Canonical tags set per route.
- 404 route is marked `noindex`.

## ✅ Performance & UX SEO Touches

- Added `preconnect` to Microsoft badge host in [index.html](index.html).
- Added lazy/async image decoding for screenshot carousel in [src/components/Screenshots.tsx](src/components/Screenshots.tsx).
- Badge container is responsive for mobile and desktop in [src/components/Download.tsx](src/components/Download.tsx).

## Google Search Console Submission (Manual Step)

Use your verified property in Search Console and submit:

1. `https://typeblitz.netlify.app/sitemap.xml`
2. `https://typeblitz.netlify.app/sitemaps/sitemap-pages.xml`
3. `https://typeblitz.netlify.app/sitemaps/sitemap-legal.xml`
4. `https://typeblitz.netlify.app/sitemaps/sitemap-media.xml`

Search Console submission requires account access and cannot be completed from local code changes alone.
