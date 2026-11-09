// Lanczos sampling adapted from image-rs/image 0.25.10, src/imageops/sample.rs.
// Distributed under image-rs' MIT license; retain the bundled image license.
use image::{DynamicImage,RgbImage};
fn sinc(t:f32)->f32 {let a=t*std::f32::consts::PI;if t==0. {1.}else{a.sin()/a}}
fn kernel(x:f32)->f32 {if x.abs()<3. {sinc(x)*sinc(x/3.)}else{0.}}
fn weights(src:usize,dst:usize)->Vec<(usize,Vec<f32>)>{
    let ratio=src as f32/dst as f32;let scale=ratio.max(1.);let support=3.*scale;
    (0..dst).map(|out|{
        let center=(out as f32+0.5)*ratio;
        let left=((center-support).floor() as i64).clamp(0,src as i64-1) as usize;
        let right=((center+support).ceil() as i64).clamp(left as i64+1,src as i64) as usize;
        let center=center-0.5;
        let mut w=Vec::with_capacity(right-left);let mut sum=0.;
        for i in left..right {let value=kernel((i as f32-center)/scale);w.push(value);sum+=value;}
        for value in &mut w {*value/=sum;}
        (left,w)
    }).collect()
}
fn accumulate(dst:&mut[f32],src:&[u8],weight:f32){
    for (d,s) in dst.iter_mut().zip(src) {*d+=*s as f32*weight;}
}
#[cfg(target_arch="x86_64")]
#[target_feature(enable="avx2")]
unsafe fn accumulate_avx2(dst:&mut[f32],src:&[u8],weight:f32){
    use std::arch::x86_64::*;
    let mut i=0;
    // Each lane performs the original separate multiply and add, in tap order.
    unsafe {
        let w=_mm256_set1_ps(weight);
        while i+8<=dst.len(){
            let bytes=_mm_loadl_epi64(src.as_ptr().add(i).cast());
            let values=_mm256_cvtepi32_ps(_mm256_cvtepu8_epi32(bytes));
            let old=_mm256_loadu_ps(dst.as_ptr().add(i));
            _mm256_storeu_ps(dst.as_mut_ptr().add(i),_mm256_add_ps(old,_mm256_mul_ps(values,w)));
            i+=8;
        }
    }
    accumulate(&mut dst[i..],&src[i..],weight);
}
fn run<const C:usize>(source:&[u8],width:usize,height:usize,nw:usize,nh:usize,simd:bool)->RgbImage{
    let vertical=weights(height,nh);let horizontal=weights(width,nw);
    let source_stride=width*C;
    let stride=source_stride+1; // one readable padding float for the final RGB SIMD load
    let mut intermediate=vec![0f32;stride*nh];
    for (y,(start,taps)) in vertical.iter().enumerate(){
        let row=&mut intermediate[y*stride..y*stride+source_stride];
        for (i,&w) in taps.iter().enumerate(){
            let src=&source[(start+i)*source_stride..(start+i+1)*source_stride];
            #[cfg(target_arch="x86_64")]
            if simd {unsafe{accumulate_avx2(row,src,w)};continue;}
            accumulate(row,src,w);
        }
    }
    let mut output=vec![0u8;nw*nh*3];
    for y in 0..nh {
        let row=&intermediate[y*stride..(y+1)*stride];
        #[cfg(target_arch="x86_64")]
        if simd && C>=3 {
            unsafe{horizontal_rgb::<C>(row,&horizontal,&mut output[y*nw*3..(y+1)*nw*3]);}
            continue;
        }
        for (x,(start,taps)) in horizontal.iter().enumerate(){
            let mut sum=[0f32;4];
            for (sample,&w) in row[start*C..(start+taps.len())*C].chunks_exact(C).zip(taps){
                for c in 0..C {sum[c]+=sample[c]*w;}
            }
            let at=(y*nw+x)*3;
            for c in 0..3 {let v=sum[if C<3 {0}else{c}];output[at+c]=v.clamp(0.,255.).round() as u8;}
        }
    }
    RgbImage::from_raw(nw as u32,nh as u32,output).unwrap()
}
#[cfg(target_arch="x86_64")]
#[target_feature(enable="avx2")]
unsafe fn horizontal_rgb<const C:usize>(row:&[f32],weights:&[(usize,Vec<f32>)],output:&mut[u8]){
    use std::arch::x86_64::*;
    // C is 3 or 4; all tap ranges lie in the source row. The caller adds a
    // readable padding float, so a final 4-float RGB load stays in bounds.
    unsafe {
        for (x,(start,taps)) in weights.iter().enumerate(){
            let mut sum=_mm_setzero_ps();
            for (i,&w) in taps.iter().enumerate(){
                let p=_mm_loadu_ps(row.as_ptr().add((start+i)*C));
                sum=_mm_add_ps(sum,_mm_mul_ps(p,_mm_set1_ps(w)));
            }
            let v=_mm_min_ps(_mm_max_ps(sum,_mm_setzero_ps()),_mm_set1_ps(255.));
            let integral=_mm_cvttps_epi32(v);
            let fraction=_mm_sub_ps(v,_mm_cvtepi32_ps(integral));
            let up=_mm_and_si128(_mm_castps_si128(_mm_cmpge_ps(fraction,_mm_set1_ps(0.5))),_mm_set1_epi32(1));
            let rounded=_mm_add_epi32(integral,up);
            let mut pixels=[0i32;4];_mm_storeu_si128(pixels.as_mut_ptr().cast(),rounded);
            output[x*3]=pixels[0] as u8;output[x*3+1]=pixels[1] as u8;output[x*3+2]=pixels[2] as u8;
        }
    }
}
pub fn resize(img:&DynamicImage,bound:u32,simd:bool)->Option<RgbImage>{
    let (w,h)=(img.width(),img.height());
    if bound==0 || w==0 || h==0 {return None;}
    let ratio=(bound as f64/w as f64).min(bound as f64/h as f64);
    let nw=((w as f64*ratio).round() as usize).max(1);let nh=((h as f64*ratio).round() as usize).max(1);
    if nw==w as usize && nh==h as usize {return Some(img.to_rgb8());}
    #[cfg(target_arch="x86_64")]
    let simd=simd && std::is_x86_feature_detected!("avx2");
    #[cfg(not(target_arch="x86_64"))]
    let simd=false;
    match img {
        DynamicImage::ImageLuma8(i)=>Some(run::<1>(i.as_raw(),w as usize,h as usize,nw,nh,simd)),
        DynamicImage::ImageLumaA8(i)=>Some(run::<2>(i.as_raw(),w as usize,h as usize,nw,nh,simd)),
        DynamicImage::ImageRgb8(i)=>Some(run::<3>(i.as_raw(),w as usize,h as usize,nw,nh,simd)),
        DynamicImage::ImageRgba8(i)=>Some(run::<4>(i.as_raw(),w as usize,h as usize,nw,nh,simd)),
        _=>None,
    }
}
