import { AuthFrame } from "@/components/auth/AuthFrame";
import { AuthForm } from "@/components/auth/AuthForm";
import { GoogleSignIn } from "@/components/auth/GoogleSignIn";
import { Alert } from "@/components/ui/Primitives";

export const metadata = { title: "Create an account · Biologix" };

export default async function RegisterPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const { error } = await searchParams;
  return (
    <AuthFrame
      title="Create an account"
      lede="Registration is open on this deployment, which suits a single lab and nothing larger."
    >
      {error && <Alert title="sign up failed">{error}</Alert>}
      <AuthForm mode="register" />
      {/* Google creates the account on first sign-in, so the same button
          serves both pages. It lands on the scientist role, which a reviewer
          then changes deliberately rather than picking in passing. */}
      <GoogleSignIn />
    </AuthFrame>
  );
}
