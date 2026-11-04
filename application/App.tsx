import React, {useCallback, useState} from 'react';
import {StatusBar, StyleSheet, View} from 'react-native';

import {SplashScreen} from './src/components/SplashScreen';
import type {Session} from './src/data/auth';
import {DashboardScreen} from './src/screens/DashboardScreen';
import {LoginScreen} from './src/screens/LoginScreen';
import {colors} from './src/theme';

function App(): React.JSX.Element {
  const [ready, setReady] = useState(false);
  const [session, setSession] = useState<Session | null>(null);

  const handleSplashDone = useCallback(() => setReady(true), []);
  const handleSignOut = useCallback(() => setSession(null), []);

  return (
    <View style={styles.root}>
      <StatusBar barStyle="light-content" backgroundColor="transparent" translucent />
      {!ready ? (
        <SplashScreen onDone={handleSplashDone} />
      ) : session ? (
        <DashboardScreen session={session} onSignOut={handleSignOut} />
      ) : (
        <LoginScreen onSignedIn={setSession} />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  root: {flex: 1, backgroundColor: colors.bg},
});

export default App;
