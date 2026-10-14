import type { Metadata } from "next";
import { Geist, Geist_Mono, Newsreader } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

const newsreader = Newsreader({
  variable: "--font-serif",
  subsets: ["latin"],
  style: ["normal", "italic"],
});

export const metadata: Metadata = {
  metadataBase: new URL(process.env.NEXT_PUBLIC_APP_URL as string),
  title: "ScholarPulse — AI Academic Research & Literature Discovery",
  description: "Federated academic discovery across OpenAlex, Semantic Scholar, arXiv, Crossref, and CORE with multi-turn AI literature synthesis.",
  keywords: [
    "academic search engine",
    "research papers",
    "OpenAlex",
    "Semantic Scholar",
    "arXiv",
    "Crossref",
    "CORE",
    "AI literature synthesis",
    "citation generator",
    "bibtex"
  ],
  authors: [{ name: "ScholarPulse Research" }],
  creator: "ScholarPulse",
  publisher: "ScholarPulse",
  alternates: {
    canonical: "/",
  },
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
    apple: "/favicon.svg",
  },
  openGraph: {
    title: "ScholarPulse — AI Academic Research & Literature Discovery",
    description: "Federated academic discovery across OpenAlex, Semantic Scholar, arXiv, Crossref, and CORE with multi-turn AI literature synthesis.",
    url: "/",
    siteName: "ScholarPulse",
    type: "website",
    locale: "en_US",
    images: [
      {
        url: "/og-image.png",
        width: 1200,
        height: 630,
        alt: "ScholarPulse: AI academic research engine",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "ScholarPulse — AI Academic Research & Literature Discovery",
    description: "Federated academic discovery across OpenAlex, Semantic Scholar, arXiv, Crossref, and CORE with multi-turn AI literature synthesis.",
    creator: "@scholarpulse",
    images: ["/og-image.png"],
  },
  robots: {
    index: true,
    follow: true,
    googleBot: {
      index: true,
      follow: true,
      "max-video-preview": -1,
      "max-image-preview": "large",
      "max-snippet": -1,
    },
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} ${newsreader.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col font-sans bg-[var(--bg-primary)] text-[var(--text-primary)]">
        {children}
      </body>
    </html>
  );
}
