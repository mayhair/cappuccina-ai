import { googleSignInUrl } from '../api/authClient'
import { GoogleIcon } from './Icons'

export function GoogleButton({ label = 'Continue with Google' }) {
  return (
    <a className="google-btn" href={googleSignInUrl}>
      <GoogleIcon />
      <span>{label}</span>
    </a>
  )
}
