import React, { useCallback, useRef, useState } from 'react';
import {
  ActivityIndicator,
  BackHandler,
  Platform,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { useFocusEffect } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { WebView, type WebViewNavigation } from 'react-native-webview';
import { useColors } from '@/hooks/useColors';

const WEB_APP_URL = 'https://www.vanshikamakeoveracademy.com/';
const PREVIEW_WEB_APP_URL = process.env.EXPO_PUBLIC_DOMAIN
  ? `https://${process.env.EXPO_PUBLIC_DOMAIN}/`
  : WEB_APP_URL;

export default function TabOneScreen() {
  const colors = useColors();
  const webViewRef = useRef<WebView>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [hasError, setHasError] = useState(false);
  const [canGoBack, setCanGoBack] = useState(false);

  useFocusEffect(
    useCallback(() => {
      if (Platform.OS !== 'android') return undefined;

      const subscription = BackHandler.addEventListener(
        'hardwareBackPress',
        () => {
          if (!canGoBack) return false;
          webViewRef.current?.goBack();
          return true;
        },
      );
      return () => subscription.remove();
    }, [canGoBack]),
  );

  const handleNavigation = (navigation: WebViewNavigation) => {
    setCanGoBack(navigation.canGoBack);
  };

  // React Native Web does not implement the native WebView load lifecycle.
  // Use a real browser iframe for the Replit preview, while native builds
  // continue using WebView with the existing back-navigation behavior.
  if (Platform.OS === 'web') {
    return (
      <View style={[styles.container, { backgroundColor: colors.background }]}>
        <iframe
          title="Vanshika Makeover Academy"
          src={PREVIEW_WEB_APP_URL}
          style={browserFrameStyle}
          allow="fullscreen"
        />
      </View>
    );
  }

  if (hasError) {
    return (
      <View style={[styles.center, { backgroundColor: colors.background }]}>
        <StatusBar style="light" />
        <Text style={[styles.errorTitle, { color: colors.primary }]}>
          Connection unavailable
        </Text>
        <Text style={[styles.errorText, { color: colors.foreground }]}>
          Please check your internet connection and try again.
        </Text>
      </View>
    );
  }

  return (
    <View style={[styles.container, { backgroundColor: colors.background }]}>
      <StatusBar style="light" />
      <WebView
        ref={webViewRef}
        source={{ uri: WEB_APP_URL }}
        style={styles.webView}
        originWhitelist={['https://*']}
        javaScriptEnabled
        domStorageEnabled
        sharedCookiesEnabled
        thirdPartyCookiesEnabled
        setSupportMultipleWindows={false}
        startInLoadingState
        onNavigationStateChange={handleNavigation}
        onLoadStart={() => {
          setHasError(false);
          setIsLoading(true);
        }}
        onLoadEnd={() => setIsLoading(false)}
        onError={() => {
          setIsLoading(false);
          setHasError(true);
        }}
      />
      {isLoading && (
        <View style={[styles.loading, { backgroundColor: colors.background }]}>
          <ActivityIndicator size="large" color={colors.primary} />
          <Text style={[styles.loadingText, { color: colors.primary }]}>
            Loading Vanshika Makeover Academy…
          </Text>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  webView: {
    flex: 1,
  },
  loading: {
    ...StyleSheet.absoluteFillObject,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 14,
  },
  loadingText: {
    fontSize: 15,
    fontWeight: '600',
  },
  center: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 28,
    gap: 12,
  },
  errorTitle: {
    fontSize: 22,
    fontWeight: '700',
  },
  errorText: {
    fontSize: 16,
    textAlign: 'center',
  },
});

const browserFrameStyle: React.CSSProperties = {
  width: '100%',
  height: '100%',
  border: 'none',
};
