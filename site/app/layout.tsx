import type { Metadata, Viewport } from 'next';
import './globals.css';
import './case.css';

const siteUrl = (process.env.NEXT_PUBLIC_SITE_URL ?? 'http://localhost:3000').replace(/\/$/, '');

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: 'Supercut × ChatCut｜特效感知的可编辑剪辑工作流',
  description: '让参考风格、真实 Beat / Onset 与叙事落进 ChatCut 多轨可编辑终版。',
  openGraph: {
    title: 'Supercut × ChatCut｜特效感知的可编辑剪辑工作流',
    description: '从参考片或原创文案到 ChatCut 多轨可编辑终版。',
    images: [{ url: `${siteUrl}/og.png`, width: 1200, height: 630, alt: 'Supercut 工作流首页' }],
  },
  twitter: {
    card: 'summary_large_image',
    title: 'Supercut × ChatCut',
    description: '参考风格、真实节拍与叙事落进可编辑终版。',
    images: [`${siteUrl}/og.png`],
  },
};

export const viewport: Viewport = { width: 'device-width', initialScale: 1 };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
