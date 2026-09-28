#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum SimdLevel {
    #[allow(dead_code)]
    Scalar,
    #[cfg(target_arch = "x86_64")]
    Sse2,
    #[cfg(target_arch = "x86_64")]
    Avx2,
    #[cfg(target_arch = "x86_64")]
    Avx512,
    #[cfg(target_arch = "aarch64")]
    Neon,
}

#[must_use]
pub(crate) fn detect_simd() -> SimdLevel {
    #[cfg(target_arch = "x86_64")]
    {
        static DETECTED: std::sync::OnceLock<SimdLevel> = std::sync::OnceLock::new();
        *DETECTED.get_or_init(|| {
            if is_x86_feature_detected!("avx512f")
                && is_x86_feature_detected!("avx512bw")
                && is_x86_feature_detected!("avx512dq")
                && is_x86_feature_detected!("avx512vl")
            {
                SimdLevel::Avx512
            } else if is_x86_feature_detected!("avx2") {
                SimdLevel::Avx2
            } else if is_x86_feature_detected!("sse2") {
                SimdLevel::Sse2
            } else {
                SimdLevel::Scalar
            }
        })
    }

    #[cfg(target_arch = "aarch64")]
    {
        SimdLevel::Neon
    }

    #[cfg(not(any(target_arch = "x86_64", target_arch = "aarch64")))]
    {
        SimdLevel::Scalar
    }
}

macro_rules! dispatch_simd {
    (
        avx512: $avx512:path,
        avx2: $avx2:path,
        sse2: $sse2:path,
        neon: $neon:path,
        scalar: $scalar:path,
        args: ($($arg:expr),* $(,)?) $(,)?
    ) => {
        match $crate::dispatch::detect_simd() {
            #[cfg(target_arch = "x86_64")]
            $crate::dispatch::SimdLevel::Avx512 => unsafe { $avx512($($arg),*) },
            #[cfg(target_arch = "x86_64")]
            $crate::dispatch::SimdLevel::Avx2 => unsafe { $avx2($($arg),*) },
            #[cfg(target_arch = "x86_64")]
            $crate::dispatch::SimdLevel::Sse2 => unsafe { $sse2($($arg),*) },
            #[cfg(target_arch = "aarch64")]
            $crate::dispatch::SimdLevel::Neon => unsafe { $neon($($arg),*) },
            $crate::dispatch::SimdLevel::Scalar => unsafe { $scalar($($arg),*) },
        }
    };
}
pub(crate) use dispatch_simd;
