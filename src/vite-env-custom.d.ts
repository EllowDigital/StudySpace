/// <reference types="vite/client" />

// This file provides custom TypeScript declarations for the project
// without modifying read-only configuration files

declare namespace JSX {
  interface IntrinsicElements {
    "ms-store-badge": {
      productid?: string;
      productname?: string;
      "window-mode"?: string;
      theme?: string;
      size?: string;
      language?: string;
      animation?: string;
      className?: string;
    };
  }
}
