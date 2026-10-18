import type { MetadataRoute } from "next";

// Minimal PWA manifest. Install prompts, icons and push arrive in Phase 7.
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Swasthya",
    short_name: "Swasthya",
    start_url: "/",
    display: "standalone",
    background_color: "#f7f3ee",
    theme_color: "#f7f3ee",
  };
}
