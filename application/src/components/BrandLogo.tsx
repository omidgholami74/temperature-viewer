import React from 'react';
import {Image, StyleSheet} from 'react-native';

type Props = {
  size?: number;
  /** `brand` paints the logo in the accent blue, `mono` in a flat colour. */
  variant?: 'brand' | 'mono' | 'plain';
  color?: string;
};

/**
 * The foundation mark, rendered from `assets/brand/mark.png`, which is derived
 * from the artwork supplied by the company (blue bird on white) with the white
 * knocked out to transparency. Keeping the bitmap means the launcher icon, the
 * splash and the header all show the real logo rather than a redrawing.
 */
export function BrandLogo({size = 48, variant = 'brand', color = '#FFFFFF'}: Props) {
  const source =
    variant === 'brand'
      ? require('../../assets/brand/mark-light.png')
      : require('../../assets/brand/mark-plain.png');

  return (
    <Image
      source={source}
      style={[
        styles.image,
        {
          width: size,
          height: size,
          tintColor: variant === 'mono' ? color : undefined,
        },
      ]}
      resizeMode="contain"
    />
  );
}

const styles = StyleSheet.create({
  image: {alignSelf: 'center'},
});
