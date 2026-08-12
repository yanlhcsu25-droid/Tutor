## 习题1-6

## 极限存在准则两个重要极限

1.计算下列极限：

(1)

$$\lim_{x\to0}\frac{\sin\omega x}{x};$$

(2)

$$\lim_{x\to0}\frac{\tan3x}{x};$$

(3)

$$\lim_{x\to0}\frac{\sin2x}{\sin5x};$$

(4)

$$\lim_{x\to0}x\cot x;$$

(5)

$$\lim_{x\to0}\frac{1-\cos2x}{x\sin x};$$

(6)$\lim_{n\to\infty}2^n\sin\frac{x}{2^n}$ (x为不等于零的常数).

解(1)当$\omega\ne0$ 时，

$$\lim_{x\to0}\frac{\sin\omega x}{x}=\lim_{x\to0}\left(\omega\cdot\frac{\sin\omega x}{\omega x}\right)=\omega\lim_{x\to0}\frac{\sin\omega x}{\omega x}=\omega;$$

当$\omega=0$ 时，

$$\lim_{x\to0}\frac{\sin\omega x}{x}=0=\omega $$

故不论ω为何值，均有$\lim_{x\to0}\frac{\sin\omega x}{x}=\omega$ 

(2)

$$\lim_{x\to0}\frac{\tan3x}{x}=\lim_{x\to0}\left(3\cdot\frac{\tan3x}{3x}\right)=3\lim_{x\to0}\frac{\tan3x}{3x}=3$$

(3)

$$\lim_{x\to0}\frac{\sin2x}{\sin5x}=\lim_{x\to0}\left(\frac{\sin2x}{2x}\cdot\frac{5x}{\sin5x}\cdot\frac{2}{5}\right)=\frac{2}{5}\lim_{x\to0}\frac{\sin2x}{2x}\cdot\lim_{x\to0}\frac{5x}{\sin5x}=\frac{2}{5}$$

(4)

$$\lim_{x\to0}x\cot x=\lim_{x\to0}\left(\frac{x}{\sin x}\cdot\cos x\right)=\lim_{x\to0}\frac{x}{\sin x}\cdot\lim_{x\to0}\cos x=1.$$

(5)

$$\lim_{x\to0}\frac{1-\cos2x}{x\sin x}=\lim_{x\to0}\frac{2\sin^2x}{x\sin x}=2\lim_{x\to0}\frac{\sin x}{x}=2$$

(6)

$$\lim_{n\to\infty}2^n\sin\frac{x}{2^n}=\lim_{n\to\infty}\left(\frac{\sin\frac{x}{2^n}}{\frac{x}{2^n}}\cdot x\right)=x.$$

2.计算下列极限：

(1)

$$\lim_{x\to0}(1-x)^{\frac{1}{x}}$$

(2)

$$\lim_{x\to0}\left(1+2x\right)^{\frac{1}{x}}$$

(3)

$$\lim_{x\to\infty}\left(\frac{1+x}{x}\right)^{2x}$$

(4)$\lim_{x\to\infty}\left(1-\frac{1}{x}\right)^{kx}$ (k为正整数).

解

(1）

$$\lim_{x\to0}\left(1-x\right)^{\frac{1}{x}}=\lim_{x\to0}\left[1+\left(-x\right)\right]^{\frac{1}{(-x)(-1)}}=\mathrm{e}^{-1}$$

(2)

$$\lim_{x\to0}\left(1+2x\right)^{\frac{1}{x}}=\lim_{x\to0}\left[\left(1+2x\right)^{\frac{1}{2x}}\right]^2=\mathrm{e}^2$$

(3)

$$\lim_{x\to\infty}\left(\frac{1+x}{x}\right)^{2x}=\lim_{x\to\infty}\left[\left(1+\frac{1}{x}\right)^x\right]^2=\mathrm{e}^2$$

课程请加QQ群：754986907，关注微信公众号（研者荣耀）获取更多考研资源
(4)

$$\lim_{x\to\infty}\left(1-\frac{1}{x}\right)^{kx}=\lim_{x\to\infty}\left[1+\frac{1}{(-x)}\right]^{(-x)(-k)}=\mathrm{e}^{-k}$$

$ 得 ^*3$ ·a 的定义，证明极限存在的准则Ⅰ

准则I′如果

$$\begin{array}{l}(1)\quad g(x)\leqslant f(x)\leqslant h(x),x\in\dot{U}(x_0,r);\\(2)\quad\lim\limits_{x\to x_0}g(x)=A,\lim\limits_{x\to x_0}h(x)=A,\end{array}那么
$$

那么$\lim_{x\to x_0}f(x)$ 存在，且等于A.

解$\forall\varepsilon>0$ ，因$\lim_{x\to x_0}g(x)=A$ ，故$\exists\delta_{1}>0$ ，当$0<\left|x-x_{0}\right|<\delta_{1}$ 时，有$\lg(x)-A\mid<\varepsilon$ ,即



$$A-\varepsilon<g(x)<A+\varepsilon,$$

又因$\lim_{x\to x_0}h(x)=A$ ，故对上面的$\varepsilon>0,\exists\delta_{2}>0$ ，当$0<\mid x-x_{0}\mid<\delta_{2}$ 时，有$|h(x)-A|<\varepsilon$ ,即



$$A-\varepsilon<h(x)<A+\varepsilon.$$

取$\delta=\min\left\{\delta_{1},\delta_{2},r\right\}$ ,则当$0<\left|x-x_{0}\right|<\delta$ 时，假设(1)及关系式(3)、(4)同时成立，从而有



$$A-\varepsilon<\pi(x)\leqslant f(x)\leqslant h(x)<A+\varepsilon,$$

即有$\left|f(x)-A\right|<\varepsilon$ .因此$\lim_{x\to x_0}f(x)$ 存在，且等于A.

注对于$x\to\infty$ 的情形，利用极限$\lim_{x\to\infty}f(x)=A$ 的定义及假设条件，可以类似地证明相应的准则I.



河4.利用极限存在准则证明：

(1)

$$\lim_{n\to\infty}\sqrt{1+\frac{1}{n}}=1$$

(2)

$$\lim_{n\to\infty}n\left(\frac{1}{n^2+\pi}+\frac{1}{n^2+2\pi}+\cdots+\frac{1}{n^2+n\pi}\right)=1$$

(3) 数列.$\sqrt{2},\sqrt{2+\sqrt{2}},\sqrt{2+\sqrt{2+\sqrt{2}}}$ ，…的极限存在；

(4)$\lim_{x\to0}\sqrt[n]{1+x}=1$ ;

(5)$\lim_{x\to0^{+}}x\left[\frac{1}{x}\right]=1.$ 

解(1)因$1<\sqrt{1+\frac{1}{n}}<1+\frac{1}{n}$ ,而$\lim_{n\to\infty}1=1,\lim_{n\to\infty}\left(1+\frac{1}{n}\right)=1$ ,由夹逼准则，即得证.



(2)因$\frac{n}{n+\pi}\leqslant n\left(\frac{1}{n^{2}+\pi}+\frac{1}{n^{2}+2\pi}+\cdots+\frac{1}{n^{2}+n\pi}\right)\leqslant\frac{n^{2}}{n^{2}+\pi}$ 而$\lim_{n\to\infty}\frac{n}{n+\pi}$ 1$\lim_{n\to\infty}\frac{n^2}{n^2+\pi}=1$ ，由夹逼准则，即得证.



套课程请加Q0群：754986907，关注微信公众号（研者荣耀）获取更多考研资源
(3)

$$x_{n+1}=\sqrt{2+x_n}\left(n\in\mathbb{N}_+\right),x_1=\sqrt{2}.$$

先证数列$\{x_{n}\}$ 有界：

$n=1$ 时，$x_{1}=\sqrt{2}<2$ ;假定$n=k$ 时，$x_{k}<2$ .当$n=k+1$ 时，$x_{k+1}=\sqrt{2+x_k}<\sqrt{2+2}=$ 2.故$x_{n}<2\left(n\in\mathbf{N}_{+}\right)$ 



再证数列$\{x_{n}\}$ 单调增加：

因

$$x_{n+1}-x_{n}=\sqrt{2+x_{n}}-x_{n}=\frac{2+x_{n}-x_{n}^{2}}{\sqrt{2+x_{n}}+x_{n}}=-\frac{\left(x_{n}-2\right)\left(x_{n}+1\right)}{\sqrt{2+x_{n}}+x_{n}}$$

由$0<x_{n}<2$ ,得$x_{n+1}-x_{n}>0$ ,即$x_{n+1}>x_{n}\left(n\in\mathbf{N}_{+}\right)$ 

由单调有界准则，即知$\lim_{n\to\infty}x_n$ 存在.

记$\lim_{n\to\infty}x_n=a$ .由$x_{n+1}=\sqrt{2+x_n}$ ,得$x_{n,+1}^{2}=2+x_{n}$ .两端同时取极限得

$$a^{2}=2+a\quad\Rightarrow\quad a^{2}-a-2=0\Rightarrow a_{1}=2,a_{2}=-1$$

即$\lim_{n\to\infty}x_n=2$ 

注本题的求解过程分成两步，第一步是证明数列$\left\{\boldsymbol{x}_{n}\right\}$ 单调有界，从而保证数列的极限存在；第二步是在递推公式两端同时取极限，得出一个含有极限值a的方程，再通过解方程求得极限值a.注意：只有在证明数列极限存在的前提下，才能采用第二步的方法求得极限值.否则，直接利用第二步，有时会导出错误的结果.

(4)当$x>0$ 时，$1<\sqrt[n]{1+x}<1+x$ ；当$-1<x<0$ 时，$1+x<\sqrt[n]{1+x}<1$ .而$\lim_{x\to0}1=1,\lim_{x\to0}(1+x)=1$ .由夹逼准则，即得证.



(5)当$x>0$ 时，$1-x<x\left[\frac{1}{x}\right]\leqslant1$ .而$\lim_{x\to0^{+}}(1-x)=1,\lim_{x\to0^{+}}1=1$ 由夹逼准则，即得证.



<div style="text-align: center;"><img src="imgs/img_in_image_box_97_859_280_898.jpg" alt="Image" width="19%" /></div>


无穷小的比较

1.当$x\to0$ 时，$,2x-x^{2}$ 与$x^{2}-x^{3}$ 相比，哪一个是高阶无穷小?

解因为$\lim_{x\to0}\left(2x-x^2\right)=0,\lim_{x\to0}\left(x^2-x^3\right)=0$ 

$$\lim_{x\to0}\frac{x^2-x^3}{2x-x^2}=\lim_{x\to0}\frac{x-x^2}{2-x}=0$$

所以当$x\to0$ 时$x^{2}-x^{3}$ 是比$2x-x^{2}$ 高阶的无穷小.

2.当$x\to0$ 时，$(1-\cos x)^{2}$ 与$\sin^{2}x$ 相比，哪一个是高阶无穷小?

解因为$\lim_{x\to0}\left(1-\cos x\right)^2=0,\lim_{x\to0}\sin^2x=0$ 

$$\lim_{x\to0}\frac{(1-\cos x)^2}{\sin^2x}=\lim_{x\to0}\frac{\left(\frac{1}{2}x^2\right)^2}{x^2}=0$$