import AsyncStorage from '@react-native-async-storage/async-storage';

/**
 * Sign-in for the app.
 *
 * The controller API has no account endpoints (it only guards ingestion with an
 * API key), so access to the dashboard is gated locally. Replace the account
 * list below with the operator accounts, or point `signIn` at a real endpoint
 * when the backend gains one.
 */
export type Session = {
  username: string;
  displayName: string;
  role: string;
};

type Account = Session & {password: string};

const ACCOUNTS: Account[] = [
  {
    username: 'admin',
    password: 'razi1400',
    displayName: 'مدیر سیستم',
    role: 'مدیر',
  },
  {
    username: 'operator',
    password: 'kore1400',
    displayName: 'اپراتور کوره',
    role: 'اپراتور',
  },
];

const STORAGE_KEY = 'razi.session';
const REVEAL_DELAY_MS = 450;

const wait = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

/** Throws a Persian message when the credentials do not match. */
export async function signIn(username: string, password: string): Promise<Session> {
  await wait(REVEAL_DELAY_MS);

  const name = username.trim().toLowerCase();
  const secret = password.trim();

  if (name === '' || secret === '') {
    throw new Error('نام کاربری و رمز عبور را وارد کنید');
  }

  const account = ACCOUNTS.find(
    item => item.username === name && item.password === secret,
  );

  if (!account) {
    throw new Error('نام کاربری یا رمز عبور درست نیست');
  }

  return {
    username: account.username,
    displayName: account.displayName,
    role: account.role,
  };
}

/** The session saved on this device, or null when the user signed out. */
export async function loadSession(): Promise<Session | null> {
  try {
    const raw = await AsyncStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return null;
    }

    const parsed: unknown = JSON.parse(raw);
    if (
      typeof parsed === 'object' &&
      parsed !== null &&
      typeof (parsed as Session).username === 'string' &&
      typeof (parsed as Session).displayName === 'string' &&
      typeof (parsed as Session).role === 'string'
    ) {
      return parsed as Session;
    }

    return null;
  } catch {
    return null;
  }
}

export async function saveSession(session: Session): Promise<void> {
  try {
    await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(session));
  } catch {
    // A failed write only means the next launch asks again.
  }
}

export async function clearSession(): Promise<void> {
  try {
    await AsyncStorage.removeItem(STORAGE_KEY);
  } catch {
    // Nothing to do: the in-memory session is cleared by the caller.
  }
}
