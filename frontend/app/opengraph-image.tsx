import { ImageResponse } from "next/og";
import { APP_NAME } from "./brand";

// Link-preview image (WhatsApp, LinkedIn, X, Slack...). Generated at build time from NEXT_PUBLIC_APP_NAME,
// so renaming the product never needs a new image file.
export const alt = `${APP_NAME}: AI academic research engine`;
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

const ACCENT = "#7C2A2A";
const INK = "#221D18";

export default function OpenGraphImage() {
  return new ImageResponse(
    (
      <div style={{ width: "100%", height: "100%", display: "flex", background: "#FAF6EF", padding: 60 }}>
        <div
          style={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            background: "#FFFFFF",
            border: "3px solid #E4DCCB",
            borderRadius: 28,
            padding: "40px 40px",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
            <div
              style={{
                width: 64,
                height: 64,
                borderRadius: 14,
                background: ACCENT,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" />
                <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" />
              </svg>
            </div>
            <div style={{ fontSize: 40, fontWeight: 700, color: INK }}>{APP_NAME}</div>
          </div>
          <div style={{ marginTop: 34, fontSize: 52, fontWeight: 700, color: INK, lineHeight: 1.15 }}>
            Federated AI Academic Research
          </div>
          <div style={{ fontSize: 52, fontWeight: 700, color: ACCENT, lineHeight: 1.15 }}>
            &amp; Literature Discovery
          </div>
          <div style={{ marginTop: 36, fontSize: 26, color: "#6E6459" }}>Search seven academic databases at once:</div>
          <div style={{ marginTop: 10, fontSize: 22, fontWeight: 700, color: INK }}>
            OpenAlex • Semantic Scholar • arXiv • Crossref • CORE • PubMed • Europe PMC
          </div>
        </div>
      </div>
    ),
    size,
  );
}
