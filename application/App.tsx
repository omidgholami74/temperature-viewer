import React, {useCallback, useEffect, useState} from 'react';
import {StatusBar, StyleSheet, View} from 'react-native';

import {SplashScreen} from './src/components/SplashScreen';
import {clearSession, loadSession, saveSession} from './src/data/auth';
import type {Session} from './src/data/auth';
import {DashboardScreen} from './src/screens/DashboardScreen';
import {LoginScreen} from './src/screens/LoginScreen';
import {colors} from './src/theme';

function App(): React.JSX.Element {
  const [ready, setReady] = useState(false);
  const [restoring, setRestoring] = useState(true);
  const [session, setSession] = useState<Session | null>(null);

  useEffect(() => {
    let cancelled = false;

    loadSession()
      .then(saved => {
        if (!cancelled && saved) {
          setSession(saved);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setRestoring(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const handleSplashDone = useCallback(() => setReady(true), []);

  const handleSignedIn = useCallback((next: Session) => {
    setSession(next);
    saveSession(next);
  }, []);

  const handleSignOut = useCallback(() => {
    setSession(null);
    clearSession();
  }, []);

  const booting = !ready || restoring;

  return (
    <View style={styles.root}>
      <StatusBar barStyle="light-content" backgroundColor="transparent" translucent />
      {booting ? (
        <SplashScreen onDone={handleSplashDone} />
      ) : session ? (
        <DashboardScreen session={session} onSignOut={handleSignOut} />
      ) : (
        <LoginScreen onSignedIn={handleSignedIn} />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  root: {flex: 1, backgroundColor: colors.bg},
});

export default App;
