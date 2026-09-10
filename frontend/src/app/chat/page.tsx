import type { Metadata, Viewport } from "next";
import { AppShell } from "@/features/chat/components/AppShell";
import "@/features/chat/styles/chat.css";

export const metadata: Metadata = {
  title: "Tin nhắn — LinguaFlow",
  description: "Trò chuyện và dịch tin nhắn theo thời gian thực.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
};

export default function ChatPage() {
  return <AppShell />;
}
