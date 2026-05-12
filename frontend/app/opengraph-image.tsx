import { ImageResponse } from "next/og";

export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default async function OGImage() {
  return new ImageResponse(
    (
      <div
        style={{
          height: "100%",
          width: "100%",
          background:
            "linear-gradient(135deg, #FF9933 0%, #FFFFFF 50%, #138808 100%)",
          display: "flex",
          flexDirection: "column",
          alignItems: "flex-start",
          justifyContent: "center",
          padding: "80px",
        }}
      >
        <div
          style={{
            fontSize: 24,
            fontWeight: 700,
            color: "#0F1B2D",
            marginBottom: 16,
            letterSpacing: 2,
            display: "flex",
          }}
        >
          BIMSAARTHI TECHNOLOGIES
        </div>
        <div
          style={{
            fontSize: 80,
            fontWeight: 800,
            color: "#0F1B2D",
            lineHeight: 1,
            marginBottom: 24,
            display: "flex",
          }}
        >
          ProcureAI
        </div>
        <div
          style={{
            fontSize: 36,
            fontWeight: 500,
            color: "#1F2D3D",
            marginBottom: 32,
            display: "flex",
          }}
        >
          AI-Powered AP State Procurement Platform
        </div>
        <div
          style={{
            fontSize: 22,
            color: "#2C3E50",
            display: "flex",
            flexDirection: "column",
            gap: 6,
          }}
        >
          <span>Drafts → Validates → Evaluates → Communicates</span>
          <span>Bilingual EN+TE · DPDP-compliant · Full audit trail</span>
        </div>
        <div
          style={{
            fontSize: 18,
            color: "#2C3E50",
            marginTop: 40,
            opacity: 0.8,
            display: "flex",
          }}
        >
          RTGS Hackathon 2026 · Government of Andhra Pradesh
        </div>
      </div>
    ),
    { ...size },
  );
}
