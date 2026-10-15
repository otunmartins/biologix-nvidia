import { AuthFrame } from "@/components/auth/AuthFrame";
import { AuthForm } from "@/components/auth/AuthForm";

export const metadata = { title: "Sign in · Biologix" };

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string }>;
}) {
  const { next } = await searchParams;
  return (
    <AuthFrame
      title="Sign in"
      lede="Your programs, campaigns and decisions, exactly where you left them."
    >
      <AuthForm mode="login" next={next} />
    </AuthFrame>
  );
}
