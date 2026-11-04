import React, {useEffect, useRef, useState} from 'react';
import {
  ActivityIndicator,
  Animated,
  Easing,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StatusBar,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import Svg, {Defs, RadialGradient, Rect, Stop} from 'react-native-svg';

import {BrandLogo} from '../components/BrandLogo';
import {signIn} from '../data/auth';
import type {Session} from '../data/auth';
import {colors, radius, space, tabular} from '../theme';

type Props = {onSignedIn: (session: Session) => void};

export function LoginScreen({onSignedIn}: Props) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [visible, setVisible] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const rise = useRef(new Animated.Value(0)).current;
  const fade = useRef(new Animated.Value(0)).current;
  const shake = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    Animated.parallel([
      Animated.timing(fade, {
        toValue: 1,
        duration: 620,
        easing: Easing.out(Easing.quad),
        useNativeDriver: true,
      }),
      Animated.timing(rise, {
        toValue: 1,
        duration: 640,
        delay: 80,
        easing: Easing.out(Easing.cubic),
        useNativeDriver: true,
      }),
    ]).start();
  }, [fade, rise]);

  const submit = async () => {
    if (busy) {
      return;
    }

    setBusy(true);
    setError(null);

    try {
      const session = await signIn(username, password);
      onSignedIn(session);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : 'ورود ناموفق بود');
      Animated.sequence([
        Animated.timing(shake, {toValue: 1, duration: 70, useNativeDriver: true}),
        Animated.timing(shake, {toValue: -1, duration: 70, useNativeDriver: true}),
        Animated.timing(shake, {toValue: 0.6, duration: 70, useNativeDriver: true}),
        Animated.timing(shake, {toValue: 0, duration: 70, useNativeDriver: true}),
      ]).start();
    } finally {
      setBusy(false);
    }
  };

  const translateY = rise.interpolate({inputRange: [0, 1], outputRange: [26, 0]});
  const translateX = shake.interpolate({inputRange: [-1, 1], outputRange: [-9, 9]});

  return (
    <View style={styles.root}>
      <StatusBar barStyle="light-content" backgroundColor="transparent" translucent />

      <Svg style={StyleSheet.absoluteFill} width="100%" height="100%">
        <Defs>
          <RadialGradient id="login-glow" cx="26%" cy="8%" r="70%">
            <Stop offset="0" stopColor="#1E5FC8" stopOpacity={0.5} />
            <Stop offset="1" stopColor="#050A17" stopOpacity={0} />
          </RadialGradient>
          <RadialGradient id="login-glow-2" cx="88%" cy="96%" r="60%">
            <Stop offset="0" stopColor="#0E3B7A" stopOpacity={0.45} />
            <Stop offset="1" stopColor="#050A17" stopOpacity={0} />
          </RadialGradient>
        </Defs>
        <Rect width="100%" height="100%" fill={colors.bg} />
        <Rect width="100%" height="100%" fill="url(#login-glow)" />
        <Rect width="100%" height="100%" fill="url(#login-glow-2)" />
      </Svg>

      <KeyboardAvoidingView
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        style={styles.flex}>
        <ScrollView
          contentContainerStyle={[
            styles.scroll,
            {paddingTop: (StatusBar.currentHeight ?? 24) + space(10)},
          ]}
          keyboardShouldPersistTaps="handled"
          showsVerticalScrollIndicator={false}>
          <Animated.View style={{opacity: fade}}>
            <View style={styles.badge}>
              <BrandLogo size={68} />
            </View>
            <Text style={styles.title}>اتوماسیون هوشمند رازی</Text>
            <Text style={styles.company}>بنیاد علوم کاربردی رازی</Text>
          </Animated.View>

          <Animated.View
            style={[
              styles.card,
              {opacity: rise, transform: [{translateY}, {translateX}]},
            ]}>
            <Text style={styles.cardTitle}>ورود به پنل پایش</Text>
            <Text style={styles.cardHint}>
              برای مشاهده دمای کوره‌ها وارد حساب کاربری خود شوید.
            </Text>

            <Text style={styles.label}>نام کاربری</Text>
            <TextInput
              value={username}
              onChangeText={text => {
                setUsername(text);
                setError(null);
              }}
              placeholder="مثلاً admin"
              placeholderTextColor={colors.muted}
              style={styles.input}
              autoCapitalize="none"
              autoCorrect={false}
              textAlign="right"
              returnKeyType="next"
              editable={!busy}
            />

            <Text style={[styles.label, {marginTop: space(4)}]}>رمز عبور</Text>
            <View style={styles.passwordRow}>
              <TextInput
                value={password}
                onChangeText={text => {
                  setPassword(text);
                  setError(null);
                }}
                placeholder="••••••••"
                placeholderTextColor={colors.muted}
                style={styles.passwordInput}
                secureTextEntry={!visible}
                autoCapitalize="none"
                autoCorrect={false}
                textAlign="right"
                returnKeyType="go"
                onSubmitEditing={submit}
                editable={!busy}
              />
              <Pressable
                onPress={() => setVisible(current => !current)}
                hitSlop={10}
                style={styles.eye}>
                <Text style={styles.eyeText}>{visible ? 'پنهان' : 'نمایش'}</Text>
              </Pressable>
            </View>

            {error ? <Text style={styles.error}>{error}</Text> : null}

            <Pressable
              onPress={submit}
              disabled={busy}
              style={({pressed}) => [
                styles.button,
                pressed && !busy && styles.buttonPressed,
                busy && styles.buttonBusy,
              ]}>
              {busy ? (
                <ActivityIndicator color="#04122B" />
              ) : (
                <Text style={styles.buttonText}>ورود</Text>
              )}
            </Pressable>

            <Text style={styles.footNote}>
              دسترسی به پنل تنها برای کاربران مجاز شرکت.
            </Text>
          </Animated.View>
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {flex: 1, backgroundColor: colors.bg},
  flex: {flex: 1},
  scroll: {
    paddingHorizontal: space(6),
    paddingBottom: space(10),
  },
  badge: {
    alignSelf: 'center',
    width: 108,
    height: 108,
    borderRadius: 30,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: 'rgba(46, 123, 255, 0.1)',
    borderWidth: 1,
    borderColor: 'rgba(124, 192, 255, 0.24)',
  },
  title: {
    ...tabular,
    marginTop: space(5),
    textAlign: 'center',
    color: colors.text,
    fontSize: 21,
    fontWeight: '800',
  },
  company: {
    marginTop: space(1.5),
    textAlign: 'center',
    color: colors.brandLight,
    fontSize: 12,
    letterSpacing: 1.1,
  },
  card: {
    marginTop: space(8),
    backgroundColor: colors.surface,
    borderRadius: radius.xl,
    borderWidth: 1,
    borderColor: colors.border,
    padding: space(5),
  },
  cardTitle: {color: colors.text, fontSize: 16, fontWeight: '800', textAlign: 'right'},
  cardHint: {
    color: colors.muted,
    fontSize: 11,
    marginTop: space(1.5),
    lineHeight: 18,
    textAlign: 'right',
  },
  label: {
    color: colors.textSoft,
    fontSize: 11,
    marginTop: space(5),
    marginBottom: space(2),
    textAlign: 'right',
  },
  input: {
    backgroundColor: colors.surfaceAlt,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    color: colors.text,
    paddingHorizontal: space(3.5),
    paddingVertical: space(3),
    fontSize: 14,
  },
  passwordRow: {
    flexDirection: 'row-reverse',
    alignItems: 'center',
    backgroundColor: colors.surfaceAlt,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    paddingLeft: space(3.5),
  },
  passwordInput: {
    flex: 1,
    color: colors.text,
    paddingHorizontal: space(3.5),
    paddingVertical: space(3),
    fontSize: 14,
  },
  eye: {paddingVertical: space(2), paddingHorizontal: space(1)},
  eyeText: {color: colors.brandLight, fontSize: 10.5, fontWeight: '700'},
  error: {
    color: colors.warm,
    fontSize: 11.5,
    marginTop: space(3),
    textAlign: 'right',
  },
  button: {
    marginTop: space(6),
    backgroundColor: colors.brandLight,
    borderRadius: radius.md,
    paddingVertical: space(3.5),
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: 50,
  },
  buttonPressed: {opacity: 0.86},
  buttonBusy: {opacity: 0.7},
  buttonText: {
    color: '#04122B',
    fontSize: 15,
    fontWeight: '800',
    letterSpacing: 0.4,
  },
  footNote: {
    color: colors.muted,
    fontSize: 10,
    textAlign: 'center',
    marginTop: space(4),
  },
});
