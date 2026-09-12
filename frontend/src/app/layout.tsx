import type { Metadata } from "next";
import { Be_Vietnam_Pro, IBM_Plex_Mono, Plus_Jakarta_Sans } from "next/font/google";
import "./globals.css";

/* Be Vietnam Pro replaces Inter: Vietnamese tone marks do not collide with
   uppercase glyphs (Ậ, Ỗ, Ừ), its narrower shape supports this density, and it
   avoids the signature look of generated interfaces. Both fonts are static, so
   every required weight must be declared. */
const beVietnam = Be_Vietnam_Pro({
  subsets: ["latin", "latin-ext", "vietnamese"],
  weight: ["400", "500", "600"],
  variable: "--font-be-vietnam",
  display: "swap",
});

const plexMono = IBM_Plex_Mono({
  subsets: ["latin", "latin-ext", "vietnamese"],
  weight: ["400", "500"],
  variable: "--font-plex-mono",
  display: "swap",
});

const plusJakarta = Plus_Jakarta_Sans({
  subsets: ["latin", "latin-ext", "vietnamese"],
  variable: "--font-plus-jakarta",
  display: "swap",
});

export const metadata: Metadata = {
  title: "LinguaFlow",
  description:
    "Nhắn tin bằng tiếng của bạn, người kia đọc bằng tiếng của họ. Dịch tự động 10 ngôn ngữ.",
  manifest: "/manifest.webmanifest",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="vi" className={`${beVietnam.variable} ${plexMono.variable} ${plusJakarta.variable}`}>
      <body>{children}</body>
    </html>
  );
}
