import "./globals.css";

export const metadata = {
  title: "Maricopa Leads Dashboard",
  description: "Simple Maricopa leads dashboard from Supabase",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
