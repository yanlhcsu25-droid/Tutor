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