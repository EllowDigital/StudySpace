// main.tsx
import { createRoot } from "react-dom/client";
import { RouterProvider, createBrowserRouter } from "react-router-dom";
import routes from "./routes";
import "./index.css";

// Add meta tags for SEO
const updateMetaTags = () => {
  // Set basic meta tags
  document.title = "TypeBlitz - Free Typing Speed Improvement App";

  // Description
  let metaDescription = document.querySelector('meta[name="description"]');
  if (!metaDescription) {
    metaDescription = document.createElement("meta");
    metaDescription.setAttribute("name", "description");
    document.head.appendChild(metaDescription);
  }
  metaDescription.setAttribute(
    "content",
    "TypeBlitz is a free typing app designed to improve typing speed and accuracy with adaptive challenges, progress tracking, and a distraction-free interface."
  );

  // Open Graph / Twitter Cards
  const metaTags = [
    { property: "og:title", content: "TypeBlitz - Free Typing Speed Improvement App" },
    {
      property: "og:description",
      content:
        "Improve typing speed and accuracy with TypeBlitz - a free, lightweight desktop application.",
    },
    { property: "og:type", content: "website" },
    { property: "og:url", content: "https://typeblitz.netlify.app" },
    { property: "og:image", content: "https://typeblitz.netlify.app/assets/images/TypeBlitz.png" },
    { property: "twitter:card", content: "summary_large_image" },
    { property: "twitter:title", content: "TypeBlitz - Free Typing Speed Improvement App" },
    {
      property: "twitter:description",
      content:
        "Improve typing speed and accuracy with TypeBlitz - a free, lightweight desktop application.",
    },
    { property: "twitter:image", content: "https://typeblitz.netlify.app/assets/images/TypeBlitz.png" },
  ];

  metaTags.forEach((tag) => {
    let metaTag = document.querySelector(`meta[property="${tag.property}"]`);
    if (!metaTag) {
      metaTag = document.createElement("meta");
      metaTag.setAttribute("property", tag.property);
      document.head.appendChild(metaTag);
    }
    metaTag.setAttribute("content", tag.content);
  });
};

// Call the function when the app starts
updateMetaTags();

// Register service worker for PWA support
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    let refreshing = false;

    navigator.serviceWorker
      .register("/service-worker.js")
      .then((registration) => {
        const requestSkipWaiting = (worker: ServiceWorker | null) => {
          worker?.postMessage({ type: "SKIP_WAITING" });
        };

        if (registration.waiting) {
          requestSkipWaiting(registration.waiting);
        }

        registration.addEventListener("updatefound", () => {
          const newWorker = registration.installing;
          if (newWorker) {
            newWorker.addEventListener("statechange", () => {
              if (newWorker.state === "installed") {
                requestSkipWaiting(newWorker);
              }
            });
          }
        });
      })
      .catch((err) => {
        console.error("Service Worker registration failed:", err);
      });

    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (refreshing) return;
      refreshing = true;
      window.location.reload();
    });
  });
}

const router = createBrowserRouter(routes, {
  future: {
    v7_startTransition: true,
    v7_relativeSplatPath: true,
  },
});

createRoot(document.getElementById("root")!).render(<RouterProvider router={router} />);
