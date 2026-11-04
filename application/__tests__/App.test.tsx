/**
 * @format
 */

import 'react-native';
import React from 'react';
import App from '../App';

// Note: import explicitly to use the types shipped with jest.
import {expect, it, jest} from '@jest/globals';

// Note: test renderer must be required after react-native.
import renderer, {act} from 'react-test-renderer';

it('renders the splash screen and unmounts cleanly', () => {
  jest.useFakeTimers();

  let tree: ReturnType<typeof renderer.create> | undefined;

  act(() => {
    tree = renderer.create(<App />);
  });

  expect(tree).toBeTruthy();

  act(() => {
    tree?.unmount();
  });

  jest.useRealTimers();
});
