import { useEffect } from "react";

const SITE_URL = "https://typeblitz.netlify.app";
const DEFAULT_IMAGE = `${SITE_URL}/assets/images/TypeBlitz.png`;

type JsonLd = Record<string, unknown> | Array<Record<string, unknown>>;

type SeoHeadProps = {
  title: string;
  description: string;
  path: string;
  type?: "website" | "article";
  image?: string;
  robots?: string;
  schema?: JsonLd;
};

const upsertMeta = (selector: string, attrs: Record<string, string>, content: string) => {
  let meta = document.querySelector<HTMLMetaElement>(selector);

  if (!meta) {
    meta = document.createElement("meta");
    Object.entries(attrs).forEach(([key, value]) => meta?.setAttribute(key, value));
    document.head.appendChild(meta);
  }

  meta.setAttribute("content", content);
};

const upsertCanonical = (href: string) => {
  let canonical = document.querySelector<HTMLLinkElement>('link[rel="canonical"]');

  if (!canonical) {
    canonical = document.createElement("link");
    canonical.setAttribute("rel", "canonical");
    document.head.appendChild(canonical);
  }

  canonical.setAttribute("href", href);
};

const SeoHead = ({
  title,
  description,
  path,
  type = "website",
  image = DEFAULT_IMAGE,
  robots = "index,follow",
  schema,
}: SeoHeadProps) => {
  useEffect(() => {
    const normalizedPath = path.startsWith("/") ? path : `/${path}`;
    const pageUrl = `${SITE_URL}${normalizedPath === "/" ? "" : normalizedPath}`;

    document.title = title;
    upsertCanonical(pageUrl);

    upsertMeta('meta[name="description"]', { name: "description" }, description);
    upsertMeta('meta[name="robots"]', { name: "robots" }, robots);

    upsertMeta('meta[property="og:title"]', { property: "og:title" }, title);
    upsertMeta('meta[property="og:description"]', { property: "og:description" }, description);
    upsertMeta('meta[property="og:type"]', { property: "og:type" }, type);
    upsertMeta('meta[property="og:url"]', { property: "og:url" }, pageUrl);
    upsertMeta('meta[property="og:image"]', { property: "og:image" }, image);

    upsertMeta('meta[name="twitter:card"]', { name: "twitter:card" }, "summary_large_image");
    upsertMeta('meta[name="twitter:title"]', { name: "twitter:title" }, title);
    upsertMeta('meta[name="twitter:description"]', { name: "twitter:description" }, description);
    upsertMeta('meta[name="twitter:image"]', { name: "twitter:image" }, image);

    const schemaScriptId = "route-jsonld";
    const previousScript = document.getElementById(schemaScriptId);

    if (previousScript) {
      previousScript.remove();
    }

    if (schema) {
      const script = document.createElement("script");
      script.id = schemaScriptId;
      script.type = "application/ld+json";
      script.text = JSON.stringify(schema);
      document.head.appendChild(script);
    }

    return () => {
      const existingScript = document.getElementById(schemaScriptId);
      if (existingScript) {
        existingScript.remove();
      }
    };
  }, [description, image, path, robots, schema, title, type]);

  return null;
};

export default SeoHead;
