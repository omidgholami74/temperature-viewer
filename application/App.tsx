import React, {useCallback, useState} from 'react';
import {StatusBar, StyleSheet, View} from 'react-native';

import {SplashScreen} from './src/components/SplashScreen';
import {DashboardScreen} from './src/screens/DashboardScreen';
import {colors} from './src/theme';

function App(): React.JSX.Element {
  const [ready, setReady] = useState(false);
  const handleSplashDone = useCallback(() => setReady(true), []);

  return (
    <View style={styles.root}>
      <StatusBar barStyle="light-content" backgroundColor="transparent" translucent />
      {ready ? <DashboardScreen /> : <SplashScreen onDone={handleSplashDone} />}
    </View>
  );
}

const styles = StyleSheet.create({
  root: {flex: 1, backgroundColor: colors.bg},
});

export default App;
