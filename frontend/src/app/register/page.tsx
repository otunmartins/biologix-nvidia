import { AuthFrame } from "@/components/auth/AuthFrame";
import { AuthForm } from "@/components/auth/AuthForm";

export const metadata = { title: "Create an account · Biologix" };

export default function RegisterPage() {
  return (
    <AuthFrame
      title="Create an account"
      lede="Registration is open on this deployment, which suits a single lab and nothing larger."
    >
      <AuthForm mode="register" />
    </AuthFrame>
  );
}
