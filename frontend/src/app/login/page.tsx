import { AuthFrame } from "@/components/auth/AuthFrame";
import { AuthForm } from "@/components/auth/AuthForm";
import { GoogleSignIn } from "@/components/auth/GoogleSignIn";
import { Alert } from "@/components/ui/Primitives";

export const metadata = { title: "Sign in · Biologix" };

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string; error?: string }>;
}) {
  const { next, error } = await searchParams;
  return (
    <AuthFrame
      title="Sign in"
      lede="Your programs, campaigns and decisions, exactly where you left them."
    >
      {/* A failed Google round trip lands back here with its reason, rather
          than rendering a JSON body at whoever pressed the button. */}
      {error && <Alert title="sign in failed">{error}</Alert>}
      <AuthForm mode="login" next={next} />
      <GoogleSignIn next={next} />
    </AuthFrame>
  );
}
