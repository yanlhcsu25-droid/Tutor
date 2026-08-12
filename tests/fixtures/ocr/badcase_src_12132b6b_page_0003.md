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