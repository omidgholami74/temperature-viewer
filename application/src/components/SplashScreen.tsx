import React, {useEffect, useRef} from 'react';
import {
  Animated,
  Easing,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import Svg, {Circle, Defs, RadialGradient, Rect, Stop} from 'react-native-svg';

import {BrandLogo} from './BrandLogo';
import {colors, radius, space, tabular} from '../theme';

type Props = {onDone: () => void};

const LOGO = 132;

/**
 * Animated launch screen: the mark settles in, a dashed halo turns, the name
 * rises and a progress bar fills before the dashboard fades in.
 */
export function SplashScreen({onDone}: Props) {
  const logoScale = useRef(new Animated.Value(0.68)).current;
  const logoOpacity = useRef(new Animated.Value(0)).current;
  const haloOpacity = useRef(new Animated.Value(0)).current;
  const spin = useRef(new Animated.Value(0)).current;
  const titleOpacity = useRef(new Animated.Value(0)).current;
  const titleShift = useRef(new Animated.Value(18)).current;
  const progress = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    Animated.parallel([
      Animated.timing(logoOpacity, {
        toValue: 1,
        duration: 650,
        easing: Easing.out(Easing.quad),
        useNativeDriver: true,
      }),
      Animated.spring(logoScale, {
        toValue: 1,
        friction: 6,
        tension: 60,
        useNativeDriver: true,
      }),
      Animated.timing(haloOpacity, {
        toValue: 1,
        duration: 900,
        delay: 150,
        useNativeDriver: true,
      }),
      Animated.timing(titleOpacity, {
        toValue: 1,
        duration: 600,
        delay: 400,
        useNativeDriver: true,
      }),
      Animated.timing(titleShift, {
        toValue: 0,
        duration: 600,
        delay: 400,
        easing: Easing.out(Easing.cubic),
        useNativeDriver: true,
      }),
      Animated.timing(progress, {
        toValue: 1,
        duration: 1900,
        delay: 200,
        easing: Easing.inOut(Easing.quad),
        useNativeDriver: false,
      }),
    ]).start();

    const loop = Animated.loop(
      Animated.timing(spin, {
        toValue: 1,
        duration: 9000,
        easing: Easing.linear,
        useNativeDriver: true,
      }),
    );
    loop.start();

    const timer = setTimeout(onDone, 2500);

    return () => {
      loop.stop();
      clearTimeout(timer);
    };
  }, [haloOpacity, logoOpacity, logoScale, onDone, progress, spin, titleOpacity, titleShift]);

  const spinRotation = spin.interpolate({
    inputRange: [0, 1],
    outputRange: ['0deg', '360deg'],
  });

  const barWidth = progress.interpolate({
    inputRange: [0, 1],
    outputRange: ['0%', '100%'],
  });

  return (
    <View style={styles.root}>
      <Svg style={StyleSheet.absoluteFill} width="100%" height="100%">
        <Defs>
          <RadialGradient id="splash-glow" cx="50%" cy="40%" r="65%">
            <Stop offset="0" stopColor="#1E5FC8" stopOpacity={0.55} />
            <Stop offset="0.55" stopColor="#0A1B3C" stopOpacity={0.35} />
            <Stop offset="1" stopColor="#03060E" stopOpacity={0} />
          </RadialGradient>
        </Defs>
        <Rect width="100%" height="100%" fill="#050A17" />
        <Rect width="100%" height="100%" fill="url(#splash-glow)" />
      </Svg>

      <View style={styles.center}>
        <View style={styles.logoStage}>
          <Animated.View style={[styles.halo, {opacity: haloOpacity}]}>
            <Animated.View style={{transform: [{rotate: spinRotation}]}}>
              <Svg width={LOGO + 56} height={LOGO + 56}>
                <Circle
                  cx={(LOGO + 56) / 2}
                  cy={(LOGO + 56) / 2}
                  r={(LOGO + 56) / 2 - 6}
                  stroke={colors.brandLight}
                  strokeOpacity={0.35}
                  strokeWidth={1.6}
                  strokeDasharray="3 12"
                  fill="none"
                />
              </Svg>
            </Animated.View>
          </Animated.View>

          <View style={styles.logoDisc}>
            <Animated.View
              style={{opacity: logoOpacity, transform: [{scale: logoScale}]}}>
              <BrandLogo size={LOGO * 0.78} />
            </Animated.View>
          </View>
        </View>

        <Animated.View
          style={{
            opacity: titleOpacity,
            transform: [{translateY: titleShift}],
            alignItems: 'center',
          }}>
          <Text style={styles.title}>اتوماسیون هوشمند رازی</Text>
          <Text style={styles.company}>بنیاد علوم کاربردی رازی</Text>

          <View style={styles.divider} />
          <Text style={styles.tagline}>پایش لحظه‌ای دمای کوره‌ها</Text>
        </Animated.View>
      </View>

      <View style={styles.footer}>
        <View style={styles.track}>
          <Animated.View style={[styles.fill, {width: barWidth}]} />
        </View>
        <Text style={styles.version}>نسخه ۱.۰.۰</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: colors.bg,
    alignItems: 'center',
    justifyContent: 'center',
  },
  center: {alignItems: 'center'},
  logoStage: {
    width: LOGO + 56,
    height: LOGO + 56,
    alignItems: 'center',
    justifyContent: 'center',
  },
  halo: {position: 'absolute', alignItems: 'center', justifyContent: 'center'},
  logoDisc: {
    width: LOGO,
    height: LOGO,
    borderRadius: LOGO / 2,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: 'rgba(46, 123, 255, 0.1)',
    borderWidth: 1,
    borderColor: 'rgba(124, 192, 255, 0.28)',
  },
  title: {
    marginTop: space(9),
    color: colors.text,
    fontSize: 25,
    fontWeight: '800',
    letterSpacing: 0.4,
  },
  company: {
    marginTop: space(1.5),
    color: colors.brandLight,
    fontSize: 13,
    letterSpacing: 1.4,
    fontWeight: '600',
  },
  divider: {
    width: 46,
    height: 2,
    borderRadius: 2,
    backgroundColor: 'rgba(124, 192, 255, 0.45)',
    marginVertical: space(4),
  },
  tagline: {color: colors.textSoft, fontSize: 13, letterSpacing: 0.6},
  footer: {
    position: 'absolute',
    bottom: 56,
    alignItems: 'center',
  },
  track: {
    width: 168,
    height: 4,
    borderRadius: radius.pill,
    backgroundColor: 'rgba(124, 176, 255, 0.16)',
    overflow: 'hidden',
  },
  fill: {
    height: '100%',
    borderRadius: radius.pill,
    backgroundColor: colors.brandLight,
  },
  version: {...tabular, marginTop: space(3), color: colors.muted, fontSize: 11},
});
