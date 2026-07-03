import Terminal from "@/components/Terminal";

// Server component shell: static frame only. Everything live is inside the
// <Terminal /> client island — the server has nothing to say about data that
// changes every three seconds.
export default function Home() {
  return (
    <main className="mx-auto max-w-5xl px-4 py-5 sm:px-6">
      <Terminal />
    </main>
  );
}
