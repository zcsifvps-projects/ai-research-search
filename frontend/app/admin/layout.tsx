import type { Metadata } from "next";
import { APP_NAME } from "../brand";

// The admin area is never indexed by search engines.
export const metadata: Metadata = {
  title: `Admin · ${APP_NAME}`,
  robots: { index: false, follow: false, nocache: true },
};

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return children;
}
