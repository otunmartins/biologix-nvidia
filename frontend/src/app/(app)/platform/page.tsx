import { Console } from "@/components/Console";
import { fetchStatus } from "@/lib/server";

export const dynamic = "force-dynamic";
export const metadata = { title: "Platform · Biologix" };

/**
 * What can actually run here.
 *
 * Read on the server so the first paint carries the real component states,
 * and so `curl` of this page distinguishes a working platform from a broken
 * one. The distinction the page exists to make: `available` means
 * "credentialled and pointed somewhere", which is far weaker than "answered a
 * real call".
 */
export default async function PlatformPage() {
  const initial = await fetchStatus();
  return <Console initial={initial} />;
}
