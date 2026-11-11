import type { Metadata } from "next";
import { APP_NAME } from "../brand";

// Shared conversations are private links: never indexed by search engines.
export const metadata: Metadata = {
  title: `Shared research · ${APP_NAME}`,
  robots: { index: false, follow: false, nocache: true },
};

export default function ShareLayout({ children }: { children: React.ReactNode }) {
  return children;
}
